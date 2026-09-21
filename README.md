# 엔젤토글 (AngelToggle)

기존 고객에게 Telegram PostBot 기반 이미지 + 버튼 게시물을 발송하기 위한 Windows 관리 프로그램입니다.

## 현재 포함
- Windows 데스크톱 UI
- SQLite WAL DB
- Telegram API 저장/고정
- Session ZIP 일괄등록
- Excel 고객 DB 업로드
- 전화번호 정규화/중복검사
- PostBot 게시물 설정
- 원클릭 사전점검
- 계정별 독립 Worker 구조
- 실시간 작업 로그

## 운영 원칙
- PostBot 이미지 + 버튼 게시물 전용
- 계정 간 병렬 처리
- 동일 계정 내부 순차 처리
- 한 계정 오류는 그 계정 Worker만 중지
- 프로그램 로컬 오류/정지는 관리자 재시작 가능
- Telegram 서버 제한 자체는 우회하지 않음
- 성공 여부 불명확 건 자동 재발송 금지
- 운영자는 가능한 한 원클릭으로 준비/검수/발송/결과 확인

## 실행
1. Python 3.11+ 설치
2. `pip install -r requirements.txt`
3. `python main.py`

## 다음 개발 단계
- Telethon 실제 세션 연결/자가진단
- 연락처 추가
- User Resolve / Telegram UID 저장
- PostBot 실제 게시물 전달
- Message ID 저장
- Atomic Claim
- 캠페인 계정 고정배정
- 실제 Worker Pool 병렬 실행
- 계정별 재검사/재시작/로컬 정지 해제
- 결과 Excel 다운로드
- Setup.exe 패키징
