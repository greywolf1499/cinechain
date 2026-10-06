import Modal from "./Modal";

export default function HandoverInterstitial({ name, onReady }: { name: string; onReady: () => void }) {
  return <Modal open title={`Pass to ${name}`} onClose={() => {}}
    dismissible={false}
    widthClassName="max-w-none h-[calc(100dvh-2rem)] w-full flex flex-col"
    bodyClassName="flex flex-1 items-center justify-center p-6">
    <div className="flex flex-col items-center gap-6 text-center">
      <span aria-hidden className="text-6xl">🎮</span>
      <h2 className="text-3xl font-bold text-zinc-100">Pass to {name}</h2>
      <p className="text-zinc-400">The next player should take the device before continuing.</p>
      <button autoFocus type="button" onClick={onReady}
        className="rounded-xl bg-accent px-6 py-4 font-semibold text-zinc-950">I'm {name}</button>
    </div>
  </Modal>;
}
