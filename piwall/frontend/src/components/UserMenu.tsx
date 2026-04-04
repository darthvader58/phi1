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
    <div className="user-menu">
      <button className="avatar-button" onClick={() => setOpen((value) => !value)} type="button">
        {image ? <img alt={name || "User"} className="avatar-image" src={image} /> : <span>{initials}</span>}
      </button>

      {open ? (
        <div className="user-menu-popover">
          <p className="user-menu-name">{name || "Driver"}</p>
          <Link href="/account" onClick={() => setOpen(false)}>
            Account
          </Link>
          <button
            className="button button-secondary"
            onClick={() => signOut({ callbackUrl: "/" })}
            type="button"
          >
            Logout
          </button>
        </div>
      ) : null}
    </div>
  );
}
