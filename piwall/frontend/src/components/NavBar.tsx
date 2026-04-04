import Link from "next/link";
import type { Session } from "next-auth";
import AuthModal from "@/components/AuthModal";
import UserMenu from "@/components/UserMenu";

type NavBarProps = {
  session: Session | null;
  googleEnabled: boolean;
};

const links = [
  { href: "/", label: "Home" },
  { href: "/lobby", label: "Lobby" },
  { href: "/strategy", label: "Strategy" },
  { href: "/season", label: "Season" },
  { href: "/account", label: "Account" }
];

export default function NavBar({ session, googleEnabled }: NavBarProps) {
  return (
    <header className="site-header">
      <nav className="nav-bar">
        <Link className="brand-lockup" href="/">
          <span className="brand-mark">PW</span>
          <span>
            <strong>Pit Wall</strong>
            <small>Race strategy simulator</small>
          </span>
        </Link>

        <div className="nav-links">
          {links.map((link) => (
            <Link href={link.href} key={link.href}>
              {link.label}
            </Link>
          ))}
        </div>

        <div className="nav-auth-slot">
          {session?.user ? (
            <UserMenu image={session.user.image} name={session.user.name} />
          ) : (
            <AuthModal
              googleEnabled={googleEnabled}
              triggerLabel={googleEnabled ? "Google OAuth" : "Login / Signup"}
            />
          )}
        </div>
      </nav>
    </header>
  );
}
