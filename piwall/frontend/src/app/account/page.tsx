import { redirect } from "next/navigation";
import AccountDashboard from "@/components/AccountDashboard";
import { auth } from "@/lib/auth";
import { getUserDashboard, upsertPlayerProfile } from "@/lib/repositories";

export const dynamic = "force-dynamic";

export default async function AccountPage() {
  const session = await auth();

  if (!session?.user?.id) {
    redirect("/");
  }

  await upsertPlayerProfile({
    id: session.user.id,
    name: session.user.name,
    email: session.user.email,
    image: session.user.image
  });

  const data = await getUserDashboard(session.user.id);

  return <AccountDashboard data={data} />;
}
