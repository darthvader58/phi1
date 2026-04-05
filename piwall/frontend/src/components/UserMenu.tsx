"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { signOut } from "next-auth/react";

type UserMenuProps = {
  name?: string | null;
  image?: string | null;
};

export default function UserMenu({ name, image }: UserMenuProps) {
  const [open, setOpen] = useState(false);

  const initials = useMemo(() => {
    return (name || "PW")
      .split(" ")
      .slice(0, 2)
      .map((part) => part[0]?.toUpperCase() ?? "")
      .join("");
  }, [name]);

  return (
    <div className="relative">
      <button
        className="w-10 h-10 rounded-full border border-pit-border bg-pit-surface text-white font-bold overflow-hidden"
        onClick={() => setOpen((value) => !value)}
        type="button"
      >
        {image ? <img alt={name || "User"} className="w-full h-full object-cover" src={image} /> : <span>{initials}</span>}
      </button>

      {open ? (
        <div className="absolute right-0 top-[calc(100%+0.75rem)] min-w-56 p-4 rounded-xl border border-pit-border bg-pit-card shadow-card z-[80]">
          <p className="text-white font-bold mb-3">{name || "Driver"}</p>
          <div className="space-y-2">
            <Link
              href="/account"
              onClick={() => setOpen(false)}
              className="block text-sm text-pit-text hover:text-white transition-colors"
            >
              Account
            </Link>
            <button className="btn-secondary w-full" onClick={() => signOut({ callbackUrl: "/" })} type="button">
              Logout
            </button>
          </div>
        </div>
      ) : null}
    </div>
  );
}
