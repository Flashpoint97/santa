"use client";

import { useRouter } from "next/navigation";
import { logout } from "@/lib/api";
import { clearToken, getToken } from "@/lib/session";

/** "Odjava": ends the session on the server (best effort), forgets the token, back to the Code screen. */
export default function LogoutLink() {
  const router = useRouter();

  function onClick() {
    const token = getToken();
    // Fire and forget: the session expires on its own if the request fails.
    if (token) logout(token).catch(() => {});
    clearToken();
    router.replace("/");
  }

  return (
    <button type="button" className="text-link" onClick={onClick}>
      Odjava
    </button>
  );
}
