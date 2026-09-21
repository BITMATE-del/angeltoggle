import { redirect } from "next/navigation";
import { createServerSupabase } from "@/lib/supabase/server";
import { createServiceSupabase } from "@/lib/supabase/service";

export async function getAdmin() {
  const supabase = await createServerSupabase();
  const { data: { user } } = await supabase.auth.getUser();
  if (!user) return null;

  const service = createServiceSupabase();
  const { data: admin } = await service
    .from("angeltoggle_admin_users")
    .select("user_id,role,active")
    .eq("user_id", user.id)
    .eq("active", true)
    .maybeSingle();

  return admin ? { user, admin, service } : null;
}

export async function requireAdmin() {
  const ctx = await getAdmin();
  if (!ctx) redirect("/login");
  return ctx;
}
