"use client";
import { useState } from "react";
import { useRouter } from "next/navigation";

export function GenerateForm({products}:{products:any[]}) {
  const [codes,setCodes]=useState<string[]>([]);
  const [busy,setBusy]=useState(false);
  const router=useRouter();

  async function submit(e:React.FormEvent<HTMLFormElement>){
    e.preventDefault(); setBusy(true);
    const fd=new FormData(e.currentTarget);
    const res=await fetch("/api/admin/licenses/generate",{
      method:"POST",headers:{"content-type":"application/json"},
      body:JSON.stringify(Object.fromEntries(fd.entries()))
    });
    const data=await res.json();
    setBusy(false);
    if(!res.ok){ alert(data.error ?? "발급 실패"); return; }
    setCodes(data.codes ?? []);
    router.refresh();
  }

  return <div className="panel">
    <div className="muted">C:\\엔젤토글&gt; 기간코드 발급</div>
    <h2 className="title">라이선스 코드 발급</h2>
    <form onSubmit={submit} className="row">
      <select className="input" name="product_id" required style={{maxWidth:220}}>
        {products.map(p=><option key={p.id} value={p.id}>{p.name} · {p.duration_days}일 · {p.max_devices}PC</option>)}
      </select>
      <input className="input" name="quantity" type="number" min="1" max="100" defaultValue="1" style={{maxWidth:110}}/>
      <input className="input" name="customer_name" placeholder="고객명(선택)" style={{maxWidth:190}}/>
      <input className="input" name="memo" placeholder="메모(선택)" style={{maxWidth:230}}/>
      <button className="btn" disabled={busy}>{busy?"발급중...":"[ 코드 발급 ]"}</button>
    </form>
    {codes.length>0 && <div style={{marginTop:14}}>
      <b>발급된 코드는 이 화면에서만 원문 확인됩니다. 바로 전달/보관하세요.</b>
      <div className="codebox">{codes.join("\n")}</div>
    </div>}
  </div>;
}
