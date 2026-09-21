# 엔젤토글 판매관리 어드민

Next.js 기반 엔젤토글 라이선스 판매관리 화면입니다.

필수 환경변수:
- NEXT_PUBLIC_SUPABASE_URL
- NEXT_PUBLIC_SUPABASE_PUBLISHABLE_KEY
- SUPABASE_SECRET_KEY

Vercel 배포 시 Root Directory를 `admin` 으로 설정합니다.

주요 기능:
- 관리자 로그인
- 기간코드 발급
- 라이선스 현황
- 사용중/미사용/정지/7일 이내 만료 집계
- 라이선스 정지/재개/폐기
- +30일 기간연장
- 기기 인증 초기화
