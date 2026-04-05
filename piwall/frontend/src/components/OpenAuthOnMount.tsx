"use client";

import { useEffect } from "react";

export default function OpenAuthOnMount() {
  useEffect(() => {
    const id = window.setTimeout(() => {
      window.dispatchEvent(new Event("pitwall-open-auth"));
    }, 0);

    return () => window.clearTimeout(id);
  }, []);

  return null;
}
