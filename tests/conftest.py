import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
# 프로젝트 루트(briefing 패키지) + tests 디렉터리(_sample_payload 등) 를 import 경로에 추가
sys.path.insert(0, str(_HERE.parent))
sys.path.insert(0, str(_HERE))
