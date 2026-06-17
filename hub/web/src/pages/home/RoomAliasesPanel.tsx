import { useState } from "react";
import { Button } from "../../components/Button";
import type { Room } from "../../lib/types";

interface Props {
  room: Room;
  onUpdate: (updated: Room) => void;
}

/**
 * Inline chip editor for Room.aliases — embedded in the FloorPlanEditor
 * right panel when a room is selected.
 */
export function RoomAliasesPanel({ room, onUpdate }: Props) {
  const [input, setInput] = useState("");

  const aliases: string[] = room.aliases ?? [];

  const addAlias = () => {
    const v = input.trim();
    if (v && !aliases.includes(v)) {
      onUpdate({ ...room, aliases: [...aliases, v] });
    }
    setInput("");
  };

  const removeAlias = (a: string) => {
    onUpdate({ ...room, aliases: aliases.filter((x) => x !== a) });
  };

  return (
    <div
      className="rounded-xl p-3 space-y-2 animate-fade-in"
      style={{ border: "1px solid var(--border-subtle)", background: "var(--card)" }}
    >
      <p className="text-xs font-mono font-medium uppercase tracking-wider text-[color:var(--text-faint)]">
        Голосові аліаси кімнати «{room.name}»
      </p>
      <div className="flex flex-wrap gap-1 min-h-6">
        {aliases.length === 0 && (
          <span className="text-xs text-[color:var(--text-faint)] italic">немає аліасів</span>
        )}
        {aliases.map((a) => (
          <span
            key={a}
            className="inline-flex items-center gap-1 bg-slate-700 text-slate-200 text-xs px-2 py-0.5 rounded-full"
          >
            {a}
            <button
              type="button"
              onClick={() => removeAlias(a)}
              className="text-slate-400 hover:text-red-400 leading-none"
            >
              ×
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          className="flex-1 rounded-lg px-3 py-1.5 text-xs transition-colors focus:outline-none focus:ring-2 focus:ring-primary-500/40"
          style={{
            background: "var(--raised)",
            border: "1px solid var(--border)",
            color: "var(--text)",
          }}
          value={input}
          placeholder='напр. "зала", "велика кімната"'
          onChange={(e) => setInput(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") { e.preventDefault(); addAlias(); }
          }}
        />
        <Button size="sm" variant="secondary" onClick={addAlias} type="button">
          +
        </Button>
      </div>
      <p className="text-xs font-mono text-[color:var(--text-faint)]">
        Слова, якими можна назвати кімнату голосом. Натисніть Enter або «+» щоб додати.
        Зберігаються разом із планом.
      </p>
    </div>
  );
}
