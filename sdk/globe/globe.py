# Imports
import json, os, re
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from dataclasses import dataclass


@dataclass
class KeplerianOrbit:
    toe: datetime
    i0: float
    idot: float
    omega: float
    omega0: float
    omegadot: float
    e: float
    a: float
    dn: float
    M0: float
    t: datetime
    toe_s: float
    

@dataclass
class EcefOrbit:
    position: np.ndarray        # ECEF position (meters)
    velocity: np.ndarray        # ECEF velocity (meters/second)
    acceleration: np.ndarray    # ECEF acceleration (meters/second^2)
    toe: datetime               # Time of ephemeris (datetime)


class Globe:
    def __init__(self):
        """Class constructor for Globe object. Initialize the ellipsoid and geoid data"""
        # WGS-84 ellipsoid constants
        self.a = 6378137.0                                    # Semi-major axis of the ellipsoid (WGS1984)
        self.b = 6356752.3142                                 # Semi-minor axis of the ellipsoid (WGS1984)
        self.f = 1/298.257223563                              # Flattening parameter
        self.e = np.sqrt((self.a**2 - self.b**2)/self.a**2)   # Eccentricity of the ellipsoid
        self.e2 = self.f * (2 - self.f)                       # First eccentricity squared
        self.R = 6371000.0                                    # Mean radius of Earth (meters)
        
        # Earth physical constants
        self.mu = 3.986005e14                                 # Earth's gravitational parameter (m³/s²)
        self.omegaEarth = 7.2921151467e-5                     # Earth's rotation rate (rad/s)
        
        # Get geoid data
        self.cwd = os.path.dirname(os.path.realpath(__file__))
        self.geoidData = pd.read_csv(os.path.join(self.cwd, 'geoidHeights.csv'))


    def llaToEcef(self, lat, lon, alt):
        """Converts ellipsoid coordinates (latitude (deg), longitude (deg), altitude (HAE-m)) to ECEF (x,y,z in meters) coordinates."""
        lat = np.radians(lat)
        lon = np.radians(lon)
        N = self.a / np.sqrt(1 - self.e2 * np.sin(lat)**2)
        x = (N + alt) * np.cos(lat) * np.cos(lon)
        y = (N + alt) * np.cos(lat) * np.sin(lon)
        z = (N * (1 - self.e2) + alt) * np.sin(lat)
        return np.array([x, y, z])


    def ecefToLla(self, ecef, precision=1e-4):
        """Robust ECEF to LLA conversion (WGS-84, iterative Bowring's method). Returns (lat, lon, alt) in degrees/meters."""
        x, y, z = ecef
        a = self.a
        b = self.b
        e2 = self.e2
        ep2 = (a**2)/(b**2) - 1
        lon = np.arctan2(y, x)
        p = np.sqrt(x**2 + y**2)
        theta = np.arctan2(z * a, p * b)
        sin_theta = np.sin(theta)
        cos_theta = np.cos(theta)
        lat = np.arctan2(z + ep2 * b * sin_theta**3, p - e2 * a * cos_theta**3)
        for _ in range(10):
            N = a / np.sqrt(1 - e2 * np.sin(lat)**2)
            alt = p / np.cos(lat) - N
            lat_new = np.arctan2(z, p * (1 - e2 * N / (N + alt)))
            if np.abs(lat - lat_new) < precision:
                break
            lat = lat_new
        return np.degrees(lat), np.degrees(lon), alt
    

    def llToNED(self, lat, lon):
        """Cartesian to NED conversion.
        Takes XYZ (m,m,m) cartesian coordinates and returns NED (m,m,m) coordinates.
        https://www.mathworks.com/help/aeroblks/directioncosinematrixeceftoned.html"""
        lat_rad, lon_rad = map(np.radians, (lat, lon))
        N = np.sin(lat_rad) * np.cos(lon_rad) - np.sin(lat_rad) * np.sin(lon_rad) + np.cos(lat_rad)
        E = -np.sin(lon_rad) + np.cos(lon_rad)
        D = -np.cos(lat_rad) * np.cos(lon_rad) - np.cos(lat_rad) * np.sin(lon_rad) - np.sin(lat_rad)
        return np.array([N, E, D])
    

    def getGeoidSeperation(self, lat, lon):
        """Returns the ellipsoidal geoid seperation (HAE to MSL variable in meters) of a given latitude and longitude (deg)."""     
        nearestLatIndex = (np.abs(self.geoidData['Latitude'] - lat)).idxmin()
        nearestLatSlice = self.geoidData[self.geoidData['Latitude'] == self.geoidData['Latitude'][nearestLatIndex]]
        nearestLonIndex = (np.abs(nearestLatSlice['Longitude'] - lon)).idxmin()
        return nearestLatSlice['GeoidHeight'][nearestLonIndex]
    

    def getDistanceHeadingPoint(self, lat, lon, distance, azimuth):
        """Returns latitude and longitude (deg) of a point at a given distance and heading, using great circle formulas."""
        Rkm = self.R / 1000.0
        distanceKm = distance / 1000.0
        lat1, lon1, azimuthRad = map(np.radians, (lat, lon, azimuth))
        lat2 = np.arcsin(np.sin(lat1) * np.cos(distanceKm / Rkm) + np.cos(lat1) * np.sin(distanceKm / Rkm) * np.cos(azimuthRad))
        lon2 = lon1 + np.arctan2(np.sin(azimuthRad) * np.sin(distanceKm / Rkm) * np.cos(lat1),
                                 np.cos(distanceKm / Rkm) - np.sin(lat1) * np.sin(lat2))
        return np.degrees(lat2), np.degrees(lon2)


    def haversine(self, lat1, lon1, lat2, lon2):
        """Returns distance between two lla (deg, deg, HAE-m) points in meters using haversine formula."""
        lat1, lon1, lat2, lon2 = map(np.radians, (lat1, lon1, lat2, lon2))
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = np.sin(dlat/2)**2 + np.cos(lat1) * np.cos(lat2) * np.sin(dlon/2)**2
        return 2 * np.arcsin(np.sqrt(a)) * self.R


    def distance(self, lla1, lla2):
        """Returns distance between two points tuples of lla (deg, deg, HAE-m) in meters, using llaToEcef conversion."""
        x1, y1, z1 = self.llaToEcef(*lla1)
        x2, y2, z2 = self.llaToEcef(*lla2)
        distance = np.sqrt((x1-x2)**2+(y1-y2)**2+(z1-z2)**2)
        return distance
    

    @staticmethod
    def ecefOrbitToEcef(orbit, obsTime):
        """Returns an ECEF orbit's ECEF position at observation time (obsTime - datetime object) using simple kinematic model"""
        dt = (obsTime - orbit.toe).total_seconds()
        return orbit.position + orbit.velocity * dt + 0.5 * orbit.acceleration * dt**2
    
    def getEcefSvAzEl(self, observerLla, obsTime, svOrbits):
        """Uses an observer's LLA (deg, deg, HAE-m) and time (obsTime - datetime object)
          to compute a mapping of svid:(az (int), el (int), timeOfEpehemeris (datetime)) 
          using svOrbits {svid: ECEF Orbit object}."""
        obsLat, obsLon, obsAlt = observerLla
        obsEcef = self.llaToEcef(obsLat, obsLon, obsAlt)
        results = {}
        for svid, orbit in svOrbits.items():
            try:
                svEcef = self.ecefOrbitToEcef(orbit, obsTime)
                enu = self.ecefToEnu(svEcef, obsEcef, obsLat, obsLon)
                az, el = self.enuToAzEl(enu)
                results[svid] = (az, el, orbit.toe)
            except Exception:
                results[svid] = (None, None, None)
        return results


    def getSvAzEl(self, observerLla, obsTime, svDict):
        """Uses an observer's LLA (deg, deg, HAE-m) and time (obsTime - datetime object)
          to compute a mapping of svid:(az (int), el (int), timeOfEpehemeris (datetime)) 
          using svOrbits {svid: Ellipsiodal Orbit object}."""
        obsLat, obsLon, obsAlt = observerLla
        obsEcef = self.llaToEcef(obsLat, obsLon, obsAlt)
        results = {}
        for svid, ephObj in svDict.items():
            try:
                svEcef = self.keplerianToEcefObj(ephObj, obsTime)
                enu = self.ecefToEnu(svEcef, obsEcef, obsLat, obsLon)
                az, el = self.enuToAzEl(enu)
                results[svid] = (az, el)
            except Exception:
                results[svid] = (None, None)
        return results
    

    @staticmethod
    def ecefToEnu(ecef, refEcef, refLat, refLon):
        """Converts ECEF coordinates (x,y,z in meters) to ENU (m,m,m) coordinates relative to a reference point."""
        refLat, refLon = np.radians(refLat), np.radians(refLon)
        dx = ecef - refEcef
        t = np.array([[-np.sin(refLon),              np.cos(refLon),             0],
                     [-np.sin(refLat)*np.cos(refLon), -np.sin(refLat)*np.sin(refLon), np.cos(refLat)],
                     [ np.cos(refLat)*np.cos(refLon),  np.cos(refLat)*np.sin(refLon), np.sin(refLat)]]
        )
        return t @ dx

    @staticmethod
    def enuToAzEl(enu):
        """Converts ENU coordinates (e,n,u in meters) to azimuth and elevation angles (degrees)."""
        e, n, u = enu
        az = np.degrees(np.arctan2(e, n)) % 360
        horDist = np.sqrt(e**2 + n**2)
        el = np.degrees(np.arctan2(u, horDist))
        return az, el

    def keplerianToEcefObj(self, ephObj, obsTime):
        """Converts a Keplerian orbit object to ECEF coordinates (x,y,z in meters) at the given observation time."""
        mu = self.mu
        omegaEarth = self.omegaEarth
        tk = (obsTime - ephObj.toe).total_seconds()
        n0 = np.sqrt(mu / ephObj.a**3)
        n = n0 + ephObj.dn
        M = ephObj.M0 + n * tk
        E = M
        for _ in range(10):
            E -= (E - ephObj.e * np.sin(E) - M) / (1 - ephObj.e * np.cos(E))
        nu = np.arctan2(np.sqrt(1 - ephObj.e**2) * np.sin(E), np.cos(E) - ephObj.e)
        u = ephObj.omega + nu
        r = ephObj.a * (1 - ephObj.e * np.cos(E))
        i = ephObj.i0 + ephObj.idot * tk
        Omega = ephObj.omega0 + (ephObj.omegadot - omegaEarth) * tk - omegaEarth * getattr(ephObj, 'toe_s', 0.0)
        xOrb = r * np.cos(u)
        yOrb = r * np.sin(u)
        x = xOrb * np.cos(Omega) - yOrb * np.cos(i) * np.sin(Omega)
        y = xOrb * np.sin(Omega) + yOrb * np.cos(i) * np.cos(Omega)
        z = yOrb * np.sin(i)
        return np.array([x, y, z])
    

    def getAzEl(self, ephemerisStr, observerLla, observerTime):
        """
        Parse ephemeris string and calculate azimuth/elevation for all satellites at a given observer 
        location tuple (deg, deg, HAE-m) and time (observerTime - datetime object)."""

        results = {}
        
        # Parse all ephemeris entries
        for entry in re.split("SupplyEphemeris", ephemerisStr)[1:]:
            jsonStart = entry.find('{')
            jsonStop = entry.rfind('}')
            if jsonStart == -1 or jsonStop == -1:
                continue
                
            constMatch = re.match(r'([A-Za-z]+)', entry.strip())
            constellation = constMatch.group(1).upper() if constMatch else ''
            
            if not constellation:
                continue
                
            try:
                eph = json.loads(entry[jsonStart:jsonStop+1])
                svid = eph.get('svid', 0)
                
                # Initialize constellation dict if not exists
                if constellation not in results:
                    results[constellation] = {}
                
                # Handle Keplerian constellations (GPS, GALILEO, BEIDOU)
                if constellation in ['GPS', 'GALILEO', 'BEIDOU']:
                    args = parseKeplerianArgsFromEphemeris(eph, constellation, observerTime)
                    ephObj = KeplerianOrbit(**args)
                    
                    try:
                        svEcef = self.keplerianToEcefObj(ephObj, observerTime)
                        obsEcef = self.llaToEcef(*observerLla)
                        enu = self.ecefToEnu(svEcef, obsEcef, observerLla[0], observerLla[1])
                        az, el = self.enuToAzEl(enu)
                        results[constellation][svid] = (az, el, ephObj.toe)
                    except Exception:
                        results[constellation][svid] = (None, None, None)
                
                # Handle ECEF constellations (GLONASS)
                if constellation == 'GLONASS':
                    orbit = parseEcefOrbitFromEphemeris(eph, constellation, observerTime)
                    
                    try:
                        svEcef = self.ecefOrbitToEcef(orbit, observerTime)
                        obsEcef = self.llaToEcef(*observerLla)
                        enu = self.ecefToEnu(svEcef, obsEcef, observerLla[0], observerLla[1])
                        az, el = self.enuToAzEl(enu)
                        results[constellation][svid] = (az, el, orbit.toe)
                    except Exception:
                        results[constellation][svid] = (None, None, None)

            except (json.JSONDecodeError, KeyError, TypeError) as e:
                print('[Globe getAzEl] ERROR: parsing entry:', e)
                continue                                                  # Skip malformed entries
        
        return results


def parseEcefOrbitFromEphemeris(eph, constellation, now):
    """Parse and scale ECEF ephemeris dict, return EcefOrbit instance. Assumes GLONASS/ECEF fields: position_m, velocity_mps, acceleration_mps2, toe_s, week."""
    
    eph = eph.copy()
   
    # Parse ECEF position, velocity, acceleration
    if 'GLONASS' in constellation.upper():
        
        # Convert from km to meters
        position = np.array([
            eph['positionX_km'] * 1000.0,
            eph['positionY_km'] * 1000.0,
            eph['positionZ_km'] * 1000.0
        ])
        velocity = np.array([
            eph['velocityX_km_s'] * 1000.0,
            eph['velocityY_km_s'] * 1000.0,
            eph['velocityZ_km_s'] * 1000.0
        ])
        acceleration = np.array([
            eph.get('accelerationX_km_s2', 0.0) * 1000.0,
            eph.get('accelerationY_km_s2', 0.0) * 1000.0,
            eph.get('accelerationZ_km_s2', 0.0) * 1000.0
        ])
    else:
        position = np.array(eph['position_m'])
        velocity = np.array(eph['velocity_mps'])
        acceleration = np.array(eph.get('acceleration_mps2', [0.0, 0.0, 0.0]))
   
   # Set timeOfEpoch (toe) for each constellation
    if 'GLONASS' in constellation.upper():              # GLONASS toe is usually given as seconds of day, week, or UTC time. Here, assume GPS week and toe_s fields are present (like other constellations). If not, fallback to now
        
        # Calculate time of epoch
        if "NT" in eph and "tb" in eph and "TauN_s" in eph:
            nt, tb, tauN = (eph.get(var, 0) for var in ["NT", "tb", "TauN_s"])

            moscowNow = now + timedelta(hours=3)
            rolloverYear = 1996 + 4 * ((moscowNow.year - 1996) // 4)
            rollover = datetime(rolloverYear, 1, 1, tzinfo=timezone.utc) - timedelta(hours=3)

            tbMinutes = tb * 15 if tb < 96 else tb

            toe = rollover + timedelta(days=nt, minutes=tbMinutes) - timedelta(seconds=tauN)

            # Subtract one day due to orolia nt += 1 day
            toe -= timedelta(days=1)

            # Subtract a day if TOE is >12h in the future (confirmed UTC-safe)
            if toe > now + timedelta(hours=12):
                toe -= timedelta(days=1)

            toe = toe.replace(microsecond=0)
           
        else:
            toe = now
            print(f'[Globe parseEcefOrbitFromEphemeris] WARNING: Missing GLONASS ephemeris data - NT: {eph.get("NT", None)} tb: {eph.get("tb", None)} TauN_s: {eph.get("TauN_s", None)} - defaulting to now ({now})')
    else:
        toe = datetime(1980,1,6,0,0,0,tzinfo=timezone.utc) 
        print('[Globe parseEcefOrbitFromEphemeris] WARNING: No GLONASS ephemeris data found, using now as time of ephemeris {now}')

    return EcefOrbit(position=position, velocity=velocity, acceleration=acceleration, toe=toe)


def parseKeplerianArgsFromEphemeris(eph, constellation, now):
    """Parse Keplerian elements from ephemeris dictionary into a arguments to create ellopsoidal orbit instance. 
    Assumes standard constellations and Keplerian fields: semiMajorAxis_sqrt_m, eccentricity, inclination_sc, 
    argumentOfPerigee_sc, longitudeOfAscendingNode_sc, meanMotionDiff_sc_s, meanAnomaly_sc, toe_s, week."""
    
    # Get data
    eph = eph.copy()
    inclination0 = eph['inclination_sc'] * np.pi
    inclinationDot = eph['inclinationRate_sc_s'] * np.pi
    argPerigee = eph['argumentOfPerigee_sc'] * np.pi
    longAscNode0 = eph['longitudeOfAscendingNode_sc'] * np.pi
    longAscNodeDot = eph['rateOfRightAscension_sc_s'] * np.pi
    meanMotionDiff = eph['meanMotionDiff_sc_s'] * np.pi
    meanAnomaly0 = eph['meanAnomaly_sc'] * np.pi
    semiMajorAxis = eph['semiMajorAxis_sqrt_m'] ** 2
    eccentricity = eph['eccentricity']

    # Adjust epoch based on constellation
    if 'GPS' in constellation.upper():
        epoch = datetime(1980,1,6,0,0,0,tzinfo=timezone.utc)
        toe = epoch + timedelta(weeks=eph['week'], seconds=eph['toe_s'])
        toeS = eph['toe_s']
    elif 'GALILEO' in constellation.upper():
        epoch = datetime(1999,8,22,0,0,0,tzinfo=timezone.utc)
        toe = epoch + timedelta(weeks=eph['week'], seconds=eph['toe_s'])
        toeS = eph['toe_s']
    elif 'BEIDOU' in constellation.upper():
        epoch = datetime(2006,1,1,0,0,0,tzinfo=timezone.utc)
        toe = epoch + timedelta(weeks=eph['week'], seconds=eph['toe_s'])
        toeS = eph['toe_s']
    else:
        print(f'[globe parseKeplerianArgsFromEphemeris] ERROR: Constellation {constellation} not found, setting toe to now!')
        toe = now
        toeS = 0.0

    # Check if we are in a new week, and adjust appropriately if so! (Found issue in orolia script where toe_s is set to 0 on week rollover, but week data is not updated appropriately!)
    # weekSeconds = 604800
    # sixHours = 21600
    # delta = (now - toe).total_seconds()
    # if abs(delta + weekSeconds) < sixHours:
    #     eph['week'] += 1
    #     return parseKeplerianArgsFromEphemeris(eph, constellation, now)

    # Return parsed arguments as a dictionary
    return dict(
        toe=toe,
        i0=inclination0,
        idot=inclinationDot,
        omega=argPerigee,
        omega0=longAscNode0,
        omegadot=longAscNodeDot,
        e=eccentricity,
        a=semiMajorAxis,
        dn=meanMotionDiff,
        M0=meanAnomaly0,
        t=now,
        toe_s=toeS
    )

    
if __name__ == '__main__':
   pass
