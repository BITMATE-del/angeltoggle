import { createHash, randomBytes } from "node:crypto";
import { NextResponse } from "next/server";
import { getAdmin } from "@/lib/admin";

function makeCode() {
  const raw = randomBytes(9).toString("hex").toUpperCase();
  return "ANGEL-" + raw.slice(0,4) + "-" + raw.slice(4,8) + "-" + raw.slice(8,12) + "-" + raw.slice(12,16);
}
function hashCode(value:string){ return createHash("sha256").update(value.trim().toUpperCase()).digest("hex"); }

export async function POST(req: Request) {
  const ctx = await getAdmin();
  if (!ctx) return NextResponse.json({error:"UNAUTHORIZED"},{status:401});
  const body = await req.json();
  const productId = String(body.product_id ?? "");
  const quantity = Math.min(100, Math.max(1, Number(body.quantity ?? 1)));
  const customerName = String(body.customer_name ?? "").slice(0,120);
  const memo = String(body.memo ?? "").slice(0,500);

  const { data: product, error } = await ctx.service
    .from("angeltoggle_license_products")
    .select("*").eq("id",productId).eq("active",true).single();
  if (error || !product) return NextResponse.json({error:"PRODUCT_NOT_FOUND"},{status:400});

  const codes:string[] = [];
  for (let i=0;i<quantity;i++) {
    const code = makeCode();
    const { error: insertError } = await ctx.service.from("angeltoggle_licenses").insert({
      code_hash: hashCode(code),
      code_prefix: code.slice(0,10),
      product_id: product.id,
      duration_days: product.duration_days,
      max_devices: product.max_devices,
      customer_name: customerName || null,
      memo: memo || null,
      created_by: ctx.user.id,
      status: "UNUSED",
    });
    if (insertError) return NextResponse.json({error:insertError.message},{status:500});
    codes.push(code);
  }
  return NextResponse.json({ok:true,codes});
}
