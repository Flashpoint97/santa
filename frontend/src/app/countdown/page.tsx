import CountdownScreen from "@/components/CountdownScreen";
import WakeGate from "@/components/WakeGate";

export default function Page() {
  return (
    <WakeGate>
      <CountdownScreen />
    </WakeGate>
  );
}
