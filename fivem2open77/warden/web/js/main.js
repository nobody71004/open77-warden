// The Warden app's pages: the home page (the servers) on the app's own address, a server's page
// (the live map and its Warden panel) on that server's address.  Both are the same page; the state
// says which address this is.
import { api, setApiBase } from "../studio/js/api.js";
import { h } from "../studio/js/ui.js";
import { initDiag } from "../studio/js/diag.js";

setApiBase("/__o77w/api");
initDiag();

async function boot() {
  const root = document.getElementById("app");
  let state;
  try {
    state = await api.get("/state");
  } catch (e) {
    root.append(h("div.w-fatal", h("h1", "OPEN77 WARDEN"), h("p", e.status === 401
      ? "This page is not signed in to the app: open it from the app's window."
      : `The app's server did not answer: ${e.message}`)));
    return;
  }
  if (state.here) {
    const { panelPage } = await import("./panel.js");
    panelPage(root, state);
  } else {
    const { homePage } = await import("./home.js");
    homePage(root, state, { auto: !location.hash.includes("stay") });
  }
}
boot();
