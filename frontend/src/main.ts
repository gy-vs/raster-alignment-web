import "./styles.css";
import { MapView } from "./mapview";
import { AppState } from "./state";
import {
  bindUi, renderComparison, renderPointPanel, renderSlotPanel, syncControls,
} from "./ui";

async function main() {
  const state = new AppState();
  await state.init();

  const canvas = document.querySelector<HTMLCanvasElement>("#map")!;
  const mapView = new MapView(canvas, state);

  const slotA = document.querySelector<HTMLElement>("#slot-a")!;
  const slotB = document.querySelector<HTMLElement>("#slot-b")!;
  const compBox = document.querySelector<HTMLElement>("#comparison")!;
  const pointBox = document.querySelector<HTMLElement>("#point-panel")!;
  const sidLabel = document.querySelector<HTMLElement>("#session-id")!;

  // Auto-frame only on the transition to having usable file(s); later
  // pan/zoom is the user's own viewport and is never overridden.
  let framedKey = "";
  const autoFrameIfNeeded = () => {
    const s = state.state;
    if (!s) return;
    const key = `${s.slots.a?.dataset_id ?? "-"}/${s.slots.b?.dataset_id ?? "-"}`;
    const haveFiles = Boolean(s.slots.a || s.slots.b);
    const samePair = key === framedKey;
    if (haveFiles && (!samePair || !state.bounds)) {
      const b = state.initialBounds();
      if (b) {
        state.setBounds(b);
        framedKey = key;
      }
    } else if (!haveFiles) {
      framedKey = "";
    }
  };

  state.subscribe(() => {
    sidLabel.textContent = state.sessionId
      ? `检查会话 ${state.sessionId.slice(0, 8)}`
      : "";

    // A band/file change can make the two bands non-comparable: never leave
    // the screen on a diff view in that state (server would refuse it).
    if (state.mode === "diff" && state.comparison &&
        !state.comparison.comparable) {
      queueMicrotask(() => state.setMode("swipe"));
    }

    renderSlotPanel(state, "a", slotA);
    renderSlotPanel(state, "b", slotB);
    if (state.comparison) renderComparison(state.comparison, compBox);
    renderPointPanel(state, pointBox);
    syncControls(state);
    autoFrameIfNeeded();
  });

  bindUi(state);
  await state.refreshState();
  autoFrameIfNeeded();

  void mapView;
}

main().catch((err) => {
  const box = document.querySelector<HTMLElement>("#fatal")!;
  box.style.display = "block";
  box.textContent = `初始化失败：${err?.message ?? err}（请确认后端已启动）`;
});
