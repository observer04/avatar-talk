"""Make FlashHead's mediapipe import lazy.

The upstream ``flash_head/utils/facecrop.py`` imports ``CPUFaceHandler`` at
module import time, which imports mediapipe.  mediapipe 0.10.9 pins
``protobuf<4`` while livekit 1.2 requires ``protobuf>=5``, so the two cannot be
installed in one image.  We never enable face crop (the control plane already
square-crops persona photos), so the import is moved into ``process_image``.

Run once during the Modal image build, right after the repo clone.
"""

from pathlib import Path

path = Path("/opt/flashhead/flash_head/utils/facecrop.py")
source = path.read_text()

IMPORT = "from flash_head.utils.cpu_face_handler import CPUFaceHandler\n"
CALL = "    face_detector = CPUFaceHandler()"

if IMPORT in source:
    source = source.replace(IMPORT, "", 1)
if CALL in source and "from flash_head.utils.cpu_face_handler import CPUFaceHandler" not in source:
    source = source.replace(
        CALL,
        "    from flash_head.utils.cpu_face_handler import CPUFaceHandler\n" + CALL,
        1,
    )

path.write_text(source)
print("patched", path)
