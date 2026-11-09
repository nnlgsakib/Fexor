import { Alert, Button, Group, Loader, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { api, url } from "../api";
import type { Member } from "../types";
import { plural, useSpace } from "../ui";

/** Selected people by id (500 per request), or every active person page by page. */
async function loadMembers(spaceId: string, ids: string[] | undefined): Promise<Member[]> {
  const out: Member[] = [];
  if (ids) {
    for (let i = 0; i < ids.length; i += 500) {
      const r = await api<{ items: Member[] }>(`/spaces/${spaceId}/members`, { query: { ids: ids.slice(i, i + 500).join(","), size: 500 } });
      out.push(...r.items);
    }
    return out;
  }
  for (let page = 1; ; page++) {
    const r = await api<{ items: Member[]; total: number }>(`/spaces/${spaceId}/members`, { query: { active: true, size: 500, page } });
    out.push(...r.items);
    if (out.length >= r.total || r.items.length === 0) return out;
  }
}

export function Cards() {
  const { spaceId, space } = useSpace();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const ids = params.get("ids")?.split(",").filter(Boolean);
  const q = useQuery({ queryKey: ["cards", spaceId, ids], queryFn: () => loadMembers(spaceId, ids) });
  const [loaded, setLoaded] = useState(0);
  const members = q.data ?? [];
  const ready = !q.isPending && loaded >= members.length;

  return (
    <div style={{ padding: 24, background: "#fff", minHeight: "100vh" }}>
      <Group className="no-print" justify="space-between" mb="lg" wrap="wrap">
        <Group>
          <Button variant="default" onClick={() => (window.history.length > 1 ? nav(-1) : nav(`/spaces/${spaceId}/people`))}>Back</Button>
          {q.isPending ? <Group gap="xs"><Loader size="xs" /><Text>Loading people…</Text></Group>
            : <Text>{plural(members.length, "card")}{space ? ` for ${space.name}` : ""}. Sized 86 × 54 mm (ID card size).</Text>}
        </Group>
        <Button onClick={() => window.print()} disabled={!ready || members.length === 0}>
          {ready ? "Print" : `Preparing ${loaded} of ${members.length}…`}
        </Button>
      </Group>
      {q.isError && <Alert color="red" variant="light" className="no-print">Couldn't load people: {q.error.message}</Alert>}
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6mm" }}>
        {members.map((m) => (
          <div key={m.id} style={{
            width: "86mm", height: "54mm", border: "1px solid #c9d1cd", borderRadius: "3mm", padding: "4mm",
            display: "flex", gap: "4mm", alignItems: "center", breakInside: "avoid", boxSizing: "border-box",
          }}>
            <img src={url(`/spaces/${spaceId}/members/${m.id}/qr.svg`)} alt="" style={{ width: "40mm", height: "40mm" }}
              onLoad={() => setLoaded((n) => n + 1)} onError={() => setLoaded((n) => n + 1)} />
            <div style={{ minWidth: 0 }}>
              <div style={{ fontSize: "8pt", color: "#1e6b58", fontWeight: 600 }}>{space?.name}</div>
              <div style={{ fontSize: "12pt", fontWeight: 700, lineHeight: 1.15, marginTop: "1.5mm" }}>{m.full_name}</div>
              <div style={{ fontSize: "9pt", marginTop: "1mm" }}>{m.external_id}</div>
              <div style={{ fontSize: "8pt", color: "#5b6662" }}>{m.title}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
