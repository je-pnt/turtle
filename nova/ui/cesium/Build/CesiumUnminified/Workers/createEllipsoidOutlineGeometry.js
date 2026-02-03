/**
 * @license
 * Cesium - https://github.com/CesiumGS/cesium
 * Version 1.133
 *
 * Copyright 2011-2022 Cesium Contributors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 *
 * Columbus View (Pat. Pend.)
 *
 * Portions licensed separately.
 * See https://github.com/CesiumGS/cesium/blob/main/LICENSE.md for full licensing details.
 */

import {
  EllipsoidOutlineGeometry_default
} from "./chunk-V36BVRDX.js";
import "./chunk-XVHLXVN3.js";
import "./chunk-TH3P77KJ.js";
import "./chunk-22LUVCO3.js";
import "./chunk-IPXMWP5Z.js";
import "./chunk-UGSQJYGE.js";
import "./chunk-JMX3P7DE.js";
import "./chunk-ZSHDRKLT.js";
import "./chunk-IHUXLSNT.js";
import "./chunk-4O5SZ2RE.js";
import "./chunk-3SQPZVG4.js";
import "./chunk-KWB7PPVO.js";
import "./chunk-O2O4FYNI.js";
import {
  defined_default
} from "./chunk-NJYFQE5B.js";

// packages/engine/Source/Workers/createEllipsoidOutlineGeometry.js
function createEllipsoidOutlineGeometry(ellipsoidGeometry, offset) {
  if (defined_default(ellipsoidGeometry.buffer, offset)) {
    ellipsoidGeometry = EllipsoidOutlineGeometry_default.unpack(
      ellipsoidGeometry,
      offset
    );
  }
  return EllipsoidOutlineGeometry_default.createGeometry(ellipsoidGeometry);
}
var createEllipsoidOutlineGeometry_default = createEllipsoidOutlineGeometry;
export {
  createEllipsoidOutlineGeometry_default as default
};
