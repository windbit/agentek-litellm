"use client";

import useAuthorized from "@/app/(dashboard)/hooks/useAuthorized";
import SubscriptionsView from "./SubscriptionsView";

export default function SubscriptionsPage() {
  const { accessToken } = useAuthorized();
  return <SubscriptionsView accessToken={accessToken} />;
}
