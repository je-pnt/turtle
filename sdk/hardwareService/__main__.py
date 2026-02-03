"""
Hardware Service Entry Point

Run the hardware service as a standalone application:
    python -m sdk.hardwareService
    
Property of Uncompromising Sensors LLC.
"""

import asyncio
import os
from .main import HardwareServiceApp

if __name__ == '__main__':
    
    # Build path to hardware-config.json (two levels up from sdk/hardwareService/)
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
        'hardware-config.json'
    )
    
    app = HardwareServiceApp(hardwareConfigPath=config_path)
    try:
        asyncio.run(app.run())
    except KeyboardInterrupt:
        print("\nHardware service stopped by user")
