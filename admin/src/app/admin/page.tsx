import { GenerateForm } from "@/components/generate-form";
import { requireAdmin } from "@/lib/admin";
import { licenseAction } from "./actions";

function fmt(v:string|null){ if(!v) return "-"; return new Date(v).toLocaleString("ko-KR",{timeZone:"Asia/Seoul"}); }

export default async function AdminPage() {
  const { service, user, admin } = await requireAdmin();
  const now = new Date().toISOString();

  const [
    {count:total},
    {count:active},
    {count:unused},
    {count:suspended},
    {count:expiring},
    {data:products},
    {data:licenses},
  ] = await Promise.all([
    service.from("angeltoggle_licenses").select("id",{count:"exact",head:true}),
    service.from("angeltoggle_licenses").select("id",{count:"exact",head:true}).eq("status","ACTIVE"),
    service.from("angeltoggle_licenses").select("id",{count:"exact",head:true}).eq("status","UNUSED"),
    service.from("angeltoggle_licenses").select("id",{count:"exact",head:true}).eq("status","SUSPENDED"),
    service.from("angeltoggle_licenses").select("id",{count:"exact",head:true}).eq("status","ACTIVE").gte("expires_at",now).lte("expires_at",new Date(Date.now()+7*86400000).toISOString()),
    service.from("angeltoggle_license_products").select("*").eq("active",true).order("duration_days"),
    service.from("angeltoggle_licenses").select("*,angeltoggle_license_products(name)").order("created_at",{ascending:false}).limit(100),
  ]);

  return <main className="shell">
    <div className="row" style={{justifyContent:"space-between"}}>
      <div><div className="muted">C:\\엔젤토글&gt; 판매관리</div><h1 className="title">엔젤토글 판매관리 어드민</h1></div>
      <div className="muted">{user.email} · {admin.role}</div>
    </div>

    <div className="grid">
      <div className="card">전체 라이선스<div className="big">{total??0}</div></div>
      <div className="card">사용중<div className="big">{active??0}</div></div>
      <div className="card">미사용 코드<div className="big">{unused??0}</div></div>
      <div className="card">정지<div className="big">{suspended??0}</div></div>
      <div className="card">7일 이내 만료<div className="big">{expiring??0}</div></div>
    </div>

    <GenerateForm products={products??[]} />

    <div className="panel">
      <h2 className="title">최근 라이선스</h2>
      <div style={{overflowX:"auto"}}>
      <table className="table">
        <thead><tr><th>코드</th><th>상품</th><th>고객</th><th>상태</th><th>활성화</th><th>만료</th><th>PC</th><th>관리</th></tr></thead>
        <tbody>{(licenses??[]).map((l:any)=><tr key={l.id}>
          <td>{l.code_prefix}••••</td>
          <td>{l.angeltoggle_license_products?.name ?? l.duration_days+"일"}</td>
          <td>{l.customer_name ?? "-"}</td>
          <td>{l.status}</td>
          <td>{fmt(l.activated_at)}</td>
          <td>{fmt(l.expires_at)}</td>
          <td>{l.max_devices}</td>
          <td>
            <form action={licenseAction} className="row">
              <input type="hidden" name="id" value={l.id}/>
              {l.status==="SUSPENDED"
                ? <button className="btn" name="action" value="resume">재개</button>
                : <button className="btn" name="action" value="suspend">정지</button>}
              <button className="btn" name="action" value="extend30">+30일</button>
              <button className="btn" name="action" value="reset_devices">기기초기화</button>
              <button className="btn danger" name="action" value="revoke">폐기</button>
            </form>
          </td>
        </tr>)}</tbody>
      </table>
      </div>
    </div>
  </main>;
}
