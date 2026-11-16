"use client";

import { useRouter } from "next/navigation";
import { useEffect, useState } from "react";
import { ApiError, getReceiverAnimation } from "@/lib/api";
import { clearToken, getToken } from "@/lib/session";
import styles from "./RevealAnimation.module.css";
import Stamp from "./ui/Stamp";

type State =
  | { kind: "loading" }
  | { kind: "failed" }
  | { kind: "ready"; url: string };

// Browsers may refuse autoplay unless the muted *property* is set; React only writes the
// attribute, so set the property when the element mounts.
function muteOnMount(video: HTMLVideoElement | null) {
  if (video) video.muted = true;
}

/** The looping, muted, inline video of the person the viewer drew. */
export default function RevealAnimation() {
  const router = useRouter();
  const [state, setState] = useState<State>({ kind: "loading" });

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/");
      return;
    }
    const controller = new AbortController();
    let objectUrl: string | null = null;
    getReceiverAnimation(token, controller.signal).then(
      (blob) => {
        if (controller.signal.aborted) return;
        objectUrl = URL.createObjectURL(blob);
        setState({ kind: "ready", url: objectUrl });
      },
      (error: unknown) => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 401) {
          clearToken();
          router.replace("/");
          return;
        }
        setState({ kind: "failed" });
      },
    );
    return () => {
      controller.abort();
      // Release the in-memory video when leaving the screen.
      if (objectUrl) URL.revokeObjectURL(objectUrl);
    };
  }, [router]);

  if (state.kind === "loading") {
    return (
      <div className={styles.postcard}>
        <div className={styles.placeholder}>
          <p aria-live="polite">Učitavam animaciju…</p>
        </div>
      </div>
    );
  }
  if (state.kind === "failed") {
    return (
      <div className={styles.postcard}>
        <div className={styles.placeholder}>
          <p role="alert" className={styles.error}>
            Animaciju trenutno ne mogu prikazati.
          </p>
        </div>
      </div>
    );
  }
  return (
    <div className={styles.postcard}>
      <span className={styles.stamp}>
        <Stamp />
      </span>
      <video
        ref={muteOnMount}
        src={state.url}
        autoPlay
        muted
        loop
        playsInline
        preload="auto"
        aria-label="Animacija osobe koju darivaš"
        className={styles.video}
      />
      <span className={`label ${styles.caption}`}>Pošta sa Sjevernog pola</span>
    </div>
  );
}
