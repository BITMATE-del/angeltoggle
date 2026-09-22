"use client";

import { useEffect, useState } from "react";
import "./admin.css";

const API="https://fgyiofykvpkxpeylcocn.supabase.co/functions/v1/angeltoggle-admin-api";

type Product={id:string;name:string;duration_days:number;max_devices:number};
type License={
  id:string;code_prefix:string;duration_days:number;max_devices:number;
  customer_name:string|null;status:string;activated_at:string|null;expires_at:string|null;
  point_balance_krw:number;sent_count:number;used_krw:number;
  angeltoggle_license_products?:{name?:string}|null;
};
type AccountUsage={
  license_id:string;account_key:string;account_label:string;
  sent_count:number;used_krw:number;last_sent_at:string|null;
};
type Dashboard={
  counts:{
    total:number;active:number;unused:number;suspended:number;expiring:number;
    total_points_krw:number;total_sent_count:number;total_used_krw:number;
  };
  products:Product[];
  licenses:License[];
  account_usage:AccountUsage[];
  point_history:any[];
  send_unit_price_krw:number;
  username?:string;
  role?:string;
  must_change_password?:boolean;
};

function fmt(v:string|null){
  if(!v)return "-";
  try{return new Date(v).toLocaleString("ko-KR",{timeZone:"Asia/Seoul"})}
  catch{return v}
}

export default function AdminPage(){
  const [token,setToken]=useState("");
  const [data,setData]=useState<Dashboard|null>(null);
  const [error,setError]=useState("");
  const [busy,setBusy]=useState(false);
  const [codes,setCodes]=useState<string[]>([]);

  useEffect(()=>{
    const saved=localStorage.getItem("angeltoggle_admin_token")||"";
    setToken(saved);
    if(saved) load(saved);
  },[]);

  async function api(method:"GET"|"POST"="GET",body?:any,overrideToken?:string){
    const t=overrideToken ?? token;
    const res=await fetch(API,{
      method,
      headers:{
        "Content-Type":"application/json",
        "X-Admin-Token":t,
      },
      body:body?JSON.stringify(body):undefined,
      cache:"no-store",
    });
    const json=await res.json();
    if(!res.ok||!json.ok) throw new Error(json.message||json.error||"요청 실패");
    return json;
  }

  async function load(t=token){
    try{
      const json=await api("GET",undefined,t);
      setData(json);setError("");
    }catch(e:any){
      setError(e.message);
      setData(null);
      localStorage.removeItem("angeltoggle_admin_token");
      setToken("");
    }
  }

  async function login(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();
    setBusy(true);setError("");
    const fd=new FormData(e.currentTarget);
    try{
      const res=await fetch(API,{
        method:"POST",
        headers:{"Content-Type":"application/json"},
        body:JSON.stringify({
          action:"login",
          username:String(fd.get("username")||""),
          password:String(fd.get("password")||""),
        }),
      });
      const json=await res.json();
      if(!res.ok||!json.ok) throw new Error(json.message||"로그인 실패");
      localStorage.setItem("angeltoggle_admin_token",json.token);
      setToken(json.token);
      setData(json);
    }catch(e:any){setError(e.message)}
    finally{setBusy(false)}
  }

  async function logout(){
    try{await api("POST",{action:"logout"})}catch{}
    localStorage.removeItem("angeltoggle_admin_token");
    setToken("");setData(null);setCodes([]);
  }

  async function generate(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();setBusy(true);setError("");
    const fd=new FormData(e.currentTarget);
    try{
      const json=await api("POST",{
        action:"generate",
        product_id:fd.get("product_id"),
        quantity:Number(fd.get("quantity")||1),
        customer_name:fd.get("customer_name"),
        customer_contact:fd.get("customer_contact"),
        memo:fd.get("memo"),
      });
      setCodes(json.codes||[]);
      setData(json);
    }catch(e:any){setError(e.message)}
    finally{setBusy(false)}
  }

  async function act(id:string,command:string){
    if(command==="revoke"&&!confirm("이 라이선스를 폐기할까요?")) return;
    setBusy(true);setError("");
    try{
      const json=await api("POST",{action:"license_action",id,command});
      setData(json);
    }catch(e:any){setError(e.message)}
    finally{setBusy(false)}
  }

  async function adjustPoints(id:string,direction:1|-1){
    const raw=prompt(direction===1?"충전할 포인트 금액(원)을 입력하세요.":"차감할 포인트 금액(원)을 입력하세요.");
    if(raw===null) return;
    const amount=Math.trunc(Number(raw.replace(/,/g,"")));
    if(!Number.isFinite(amount)||amount<=0){ alert("올바른 금액을 입력하세요."); return; }
    const memo=prompt("메모를 입력하세요. (선택)")||"";
    const signed=direction===1?amount:-amount;
    if(!confirm((direction===1?"충전 ":"차감 ")+amount.toLocaleString()+"원을 적용할까요?")) return;
    setBusy(true);setError("");
    try{
      const json=await api("POST",{action:"adjust_points",id,amount_krw:signed,memo});
      setData(json);
    }catch(e:any){setError(e.message)}
    finally{setBusy(false)}
  }

  async function changePassword(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault();setBusy(true);setError("");
    const fd=new FormData(e.currentTarget);
    try{
      await api("POST",{
        action:"change_password",
        current_password:fd.get("current_password"),
        new_password:fd.get("new_password"),
      });
      alert("비밀번호가 변경되었습니다. 다시 로그인하세요.");
      await logout();
    }catch(e:any){setError(e.message)}
    finally{setBusy(false)}
  }

  if(!token||!data){
    return <main className="loginWrap">
      <section className="panel loginPanel">
        <div className="command">C:\\엔젤토글&gt; 판매관리 로그인</div>
        <h1>엔젤토글 판매관리</h1>
        <form onSubmit={login} className="loginForm">
          <input name="username" defaultValue="admin" placeholder="관리자 아이디" required/>
          <input name="password" type="password" placeholder="관리자 비밀번호" required/>
          <button disabled={busy}>{busy?"확인 중...":"[ 관리자 로그인 ]"}</button>
        </form>
        {error&&<div className="error">{error}</div>}
      </section>
    </main>
  }

  return <main className="page">
    <div className="head">
      <div>
        <div className="command">C:\\엔젤토글&gt; 판매관리</div>
        <h1>엔젤토글 판매관리 어드민</h1>
        <p>기간코드 · PC 인증 · 발송포인트 · 계정별 발송량 · 만료 · 정지 · 기기초기화</p>
      </div>
      <div className="headActions">
        <span>{data.username} · {data.role}</span>
        <button onClick={()=>load()}>새로고침</button>
        <button onClick={logout}>로그아웃</button>
      </div>
    </div>

    {error&&<div className="error">{error}</div>}

    {data.must_change_password&&<section className="panel warnPanel">
      <b>초기 비밀번호를 사용 중입니다. 새 비밀번호로 변경하세요.</b>
      <form onSubmit={changePassword} className="form">
        <input name="current_password" type="password" placeholder="현재 비밀번호" required/>
        <input name="new_password" type="password" placeholder="새 비밀번호 (10자 이상)" required/>
        <button disabled={busy}>비밀번호 변경</button>
      </form>
    </section>}

    <section className="cards">
      <article><span>전체 라이선스</span><b>{data.counts.total}</b></article>
      <article><span>사용중</span><b>{data.counts.active}</b></article>
      <article><span>전체 잔여 포인트</span><b>{Number(data.counts.total_points_krw||0).toLocaleString()}원</b></article>
      <article><span>누적 발송 성공</span><b>{Number(data.counts.total_sent_count||0).toLocaleString()}건</b></article>
      <article><span>누적 사용 포인트</span><b>{Number(data.counts.total_used_krw||0).toLocaleString()}원</b></article>
      <article><span>건당 발송비</span><b>{Number(data.send_unit_price_krw||10).toLocaleString()}원</b></article>
    </section>

    <section className="panel">
      <div className="command">C:\\엔젤토글&gt; 기간코드 발급</div>
      <h2>라이선스 코드 발급</h2>
      <form onSubmit={generate} className="form">
        <select name="product_id" required defaultValue="">
          <option value="" disabled>상품 선택</option>
          {data.products.map(p=><option key={p.id} value={p.id}>{p.name} · {p.duration_days}일 · {p.max_devices}PC</option>)}
        </select>
        <input name="quantity" type="number" min="1" max="100" defaultValue="1"/>
        <input name="customer_name" placeholder="고객명"/>
        <input name="customer_contact" placeholder="연락처 / 텔레그램"/>
        <input name="memo" placeholder="메모"/>
        <button disabled={busy}>코드 발급</button>
      </form>
      {codes.length>0&&<div className="codes">
        <strong>발급된 코드는 이 화면에서만 원문 확인됩니다.</strong>
        <pre>{codes.join("\n")}</pre>
      </div>}
    </section>

    <section className="panel">
      <div className="command">C:\\엔젤토글&gt; 라이선스 목록</div>
      <h2>최근 라이선스</h2>
      <div className="tableWrap">
        <table>
          <thead><tr><th>코드</th><th>상품</th><th>고객</th><th>상태</th><th>포인트</th><th>발송</th><th>사용금액</th><th>활성화</th><th>만료</th><th>PC</th><th>관리</th></tr></thead>
          <tbody>{data.licenses.map(l=><tr key={l.id}>
            <td>{l.code_prefix}••••</td>
            <td>{l.angeltoggle_license_products?.name||l.duration_days+"일"}</td>
            <td>{l.customer_name||"-"}</td>
            <td><span className="status">{l.status}</span></td>
            <td><b>{Number(l.point_balance_krw||0).toLocaleString()}원</b></td>
            <td>{Number(l.sent_count||0).toLocaleString()}건</td>
            <td>{Number(l.used_krw||0).toLocaleString()}원</td>
            <td>{fmt(l.activated_at)}</td>
            <td>{fmt(l.expires_at)}</td>
            <td>{l.max_devices}</td>
            <td><div className="actions">
              {l.status==="SUSPENDED"
                ?<button onClick={()=>act(l.id,"resume")} disabled={busy}>재개</button>
                :<button onClick={()=>act(l.id,"suspend")} disabled={busy}>정지</button>}
              <button onClick={()=>act(l.id,"extend30")} disabled={busy}>+30일</button>
              <button onClick={()=>adjustPoints(l.id,1)} disabled={busy}>포인트 충전</button>
              <button onClick={()=>adjustPoints(l.id,-1)} disabled={busy}>포인트 차감</button>
              <button onClick={()=>act(l.id,"reset_devices")} disabled={busy}>기기초기화</button>
              <button className="danger" onClick={()=>act(l.id,"revoke")} disabled={busy}>폐기</button>
            </div></td>
          </tr>)}</tbody>
        </table>
      </div>
    </section>

    <section className="panel">
      <div className="command">C:\\엔젤토글&gt; Telegram 계정별 발송 사용량</div>
      <h2>계정별 발송 통계</h2>
      <div className="tableWrap">
        <table>
          <thead><tr><th>라이선스</th><th>Telegram 계정</th><th>발송 성공</th><th>사용 포인트</th><th>최근 발송</th></tr></thead>
          <tbody>
            {(data.account_usage||[]).map((u,i)=><tr key={u.license_id+"-"+u.account_key+"-"+i}>
              <td>{data.licenses.find(l=>l.id===u.license_id)?.code_prefix||u.license_id.slice(0,8)}••••</td>
              <td>{u.account_label||u.account_key}</td>
              <td>{Number(u.sent_count||0).toLocaleString()}건</td>
              <td>{Number(u.used_krw||0).toLocaleString()}원</td>
              <td>{fmt(u.last_sent_at)}</td>
            </tr>)}
            {(data.account_usage||[]).length===0&&<tr><td colSpan={5}>아직 발송 성공 이력이 없습니다.</td></tr>}
          </tbody>
        </table>
      </div>
    </section>
  </main>;
}
