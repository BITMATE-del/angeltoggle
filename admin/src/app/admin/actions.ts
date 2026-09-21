"use server";
import { revalidatePath } from "next/cache";
import { requireAdmin } from "@/lib/admin";

export async function logout() {
  const { user } = await requireAdmin();
  const { createServerSupabase } = await import("@/lib/supabase/server");
  const client = await createServerSupabase();
  await client.auth.signOut();
}

export async function licenseAction(formData: FormData) {
  const { service } = await requireAdmin();
  const id = String(formData.get("id") ?? "");
  const action = String(formData.get("action") ?? "");
  if (!id) return;

  if (action === "suspend") {
    await service.from("angeltoggle_licenses").update({status:"SUSPENDED",updated_at:new Date().toISOString()}).eq("id",id);
  } else if (action === "resume") {
    await service.from("angeltoggle_licenses").update({status:"ACTIVE",updated_at:new Date().toISOString()}).eq("id",id);
  } else if (action === "revoke") {
    await service.from("angeltoggle_licenses").update({status:"REVOKED",updated_at:new Date().toISOString()}).eq("id",id);
  } else if (action === "extend30") {
    const { data } = await service.from("angeltoggle_licenses").select("expires_at").eq("id",id).single();
    const base = data?.expires_at && new Date(data.expires_at) > new Date() ? new Date(data.expires_at) : new Date();
    base.setDate(base.getDate()+30);
    await service.from("angeltoggle_licenses").update({expires_at:base.toISOString(),status:"ACTIVE",updated_at:new Date().toISOString()}).eq("id",id);
  } else if (action === "reset_devices") {
    await service.from("angeltoggle_license_devices").delete().eq("license_id",id);
  }

  await service.from("angeltoggle_license_events").insert({license_id:id,event_type:"ADMIN_"+action.toUpperCase()});
  revalidatePath("/admin");
}
