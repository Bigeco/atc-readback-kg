// Static-site adapter: answers the same /api/* paths as src/demo/server.py from precomputed JSON.
(function () {
  const cache = {};
  const load = (name) => (cache[name] ||= fetch(`data/${name}`).then((r) => { if (!r.ok) throw new Error(name); return r.json(); }));

  async function flightFile(id) {
    const flights = await load("flights.json");
    const f = flights.find((x) => x.flight === id);
    if (!f) throw new Error(`unknown flight ${id}`);
    return load(`flight/${f.file}`);
  }

  window.staticApi = async function (path) {
    const url = new URL(path, "http://local");
    const p = Object.fromEntries(url.searchParams.entries());
    switch (url.pathname) {
      case "/api/summary":
        return load("summary.json");
      case "/api/flights": {
        const flights = await load("flights.json");
        const q = (p.q || "").trim().toUpperCase();
        const t = p.t !== undefined && p.t !== "null" ? Number(p.t) : null;
        const win = Number(p.window || 600);
        let rows = flights.filter((f) => {
          if (q) return f.flight.includes(q) || (f.surfaces || []).some((s) => s.toUpperCase().includes(q));
          if (t === null) return true;
          return f.tx_times.some((x) => Math.abs(x - t) <= win);
        });
        rows.sort((a, b) => (t === null ? (b.last || 0) - (a.last || 0) : Math.abs((a.last || 0) - t) - Math.abs((b.last || 0) - t)));
        return rows.slice(0, 120);
      }
      case "/api/flight/timeline":
        return (await flightFile(p.id)).timeline;
      case "/api/flight/graph":
        return (await flightFile(p.id)).graph;
      case "/api/snapshot": {
        const t = Number(p.t);
        const out = {};
        (await load("states.json")).forEach((s) => {
          if (s.since <= t && t < (s.valid_to ?? Infinity)) (out[s.type] ||= []).push(s);
        });
        Object.values(out).forEach((rows) => rows.sort((a, b) => a.since - b.since));
        return out;
      }
      case "/api/alerts":
        return load("alerts.json");
      case "/api/tx": {
        const tx = (await load("tx.json"))[p.id];
        if (!tx) throw new Error(`unknown transmission ${p.id}`);
        return tx;
      }
      default:
        throw new Error(`unsupported path ${url.pathname}`);
    }
  };
})();
