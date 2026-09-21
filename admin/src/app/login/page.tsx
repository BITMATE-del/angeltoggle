import { login } from "./actions";

export default async function Login({searchParams}:{searchParams:Promise<{error?:string}>}) {
  const params = await searchParams;
  return <main className="shell" style={{maxWidth:520}}>
    <div className="panel">
      <div className="muted">C:\\엔젤토글&gt; 판매관리 로그인</div>
      <h1 className="title">엔젤토글 판매관리</h1>
      {params.error && <p style={{color:"#ff8f8f"}}>로그인 정보를 확인하세요.</p>}
      <form action={login}>
        <p><input className="input" name="email" type="email" placeholder="관리자 이메일" required /></p>
        <p><input className="input" name="password" type="password" placeholder="비밀번호" required /></p>
        <button className="btn" type="submit">[ 관리자 로그인 ]</button>
      </form>
    </div>
  </main>;
}
