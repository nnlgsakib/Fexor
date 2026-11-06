import { Button, Group, Text } from "@mantine/core";
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api, url } from "../api";
import type { Member } from "../types";
import { useSpace } from "../ui";

export function Cards() {
  const { spaceId, space } = useSpace();
  const [params] = useSearchParams();
  const ids = params.get("ids")?.split(",").filter(Boolean);
  const q = useQuery({
    queryKey: ["cards", spaceId],
    queryFn: () => api<{ items: Member[] }>(`/spaces/${spaceId}/members`, { query: { active: true, size: 500 } }),
  });
  const members = (q.data?.items ?? []).filter((m) => !ids || ids.includes(m.id));

  return (
    <div style={{ padding: 24, background: "#fff", minHeight: "100vh" }}>
      <Group className="no-print" justify="space-between" mb="lg">
        <Text>{members.length} cards for {space?.name}. Cards are sized 86 × 54 mm (ID card size).</Text>
        <Button onClick={() => window.print()}>Print</Button>
      </Group>
      <div style={{ display: "flex", flexWrap: "wrap", gap: "6mm" }}>
        {members.map((m) => (
          <div key={m.id} style={{
            width: "86mm", height: "54mm", border: "1px solid #c9d1cd", borderRadius: "3mm", padding: "4mm",
            display: "flex", gap: "4mm", alignItems: "center", breakInside: "avoid", boxSizing: "border-box",
          }}>
            <img src={url(`/spaces/${spaceId}/members/${m.id}/qr.svg`)} alt="" style={{ width: "40mm", height: "40mm" }} />
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
