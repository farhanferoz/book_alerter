// Pure cron parse/serialize for the visual schedule builder.
//
// Recognises four shapes:
//   • Every N minutes        →  `*/N * * * *`         (N must divide 60)
//   • Every N hours @ minute →  `M */N * * *`         (N must divide 24, M ∈ 0..59)
//                            or `M * * * *`            (N=1 equivalent form)
//   • Daily at HH:MM         →  `M H * * *`
//   • Weekly at HH:MM on D   →  `M H * * D`           (D ∈ 0..6, Sun=0)
//
// Anything else (ranges, lists, step on non-divisors, exotic dom/month) returns
// null so the caller can fall back to a raw text input.

export type Schedule =
  | { mode: "minutes"; every: number }
  | { mode: "hours"; every: number; minute: number }
  | { mode: "daily"; hour: number; minute: number }
  | { mode: "weekly"; dayOfWeek: number; hour: number; minute: number };

export const MINUTE_INTERVALS = [2, 5, 10, 15, 20, 30] as const;
export const HOUR_INTERVALS = [1, 2, 3, 4, 6, 8, 12] as const;

export const DAY_NAMES = [
  "Sunday",
  "Monday",
  "Tuesday",
  "Wednesday",
  "Thursday",
  "Friday",
  "Saturday",
] as const;

function asInt(field: string, max: number): number | null {
  if (!/^\d+$/.test(field)) return null;
  const n = Number(field);
  return n >= 0 && n <= max ? n : null;
}

export function parseCron(cron: string): Schedule | null {
  const fields = cron.trim().split(/\s+/);
  if (fields.length !== 5) return null;
  const [m, h, dom, month, dow] = fields;
  if (month !== "*" || dom !== "*") return null;

  const minuteStep = /^\*\/(\d+)$/.exec(m);
  if (minuteStep && h === "*" && dow === "*") {
    const every = Number(minuteStep[1]);
    if ((MINUTE_INTERVALS as readonly number[]).includes(every)) {
      return { mode: "minutes", every };
    }
    return null;
  }

  const minute = asInt(m, 59);
  if (minute === null) return null;

  const hourStep = /^\*\/(\d+)$/.exec(h);
  if (hourStep && dow === "*") {
    const every = Number(hourStep[1]);
    if ((HOUR_INTERVALS as readonly number[]).includes(every)) {
      return { mode: "hours", every, minute };
    }
    return null;
  }

  if (h === "*" && dow === "*") {
    return { mode: "hours", every: 1, minute };
  }

  const hour = asInt(h, 23);
  if (hour === null) return null;

  if (dow === "*") {
    return { mode: "daily", hour, minute };
  }

  const dayOfWeek = asInt(dow, 6);
  if (dayOfWeek === null) return null;
  return { mode: "weekly", dayOfWeek, hour, minute };
}

export function serializeCron(s: Schedule): string {
  switch (s.mode) {
    case "minutes":
      return `*/${s.every} * * * *`;
    case "hours":
      return s.every === 1
        ? `${s.minute} * * * *`
        : `${s.minute} */${s.every} * * *`;
    case "daily":
      return `${s.minute} ${s.hour} * * *`;
    case "weekly":
      return `${s.minute} ${s.hour} * * ${s.dayOfWeek}`;
  }
}

function pad2(n: number): string {
  return n < 10 ? `0${n}` : String(n);
}

export function describeSchedule(s: Schedule): string {
  switch (s.mode) {
    case "minutes":
      return `Every ${s.every} minutes`;
    case "hours": {
      const at = s.minute === 0 ? "" : ` at minute ${s.minute}`;
      return s.every === 1 ? `Every hour${at}` : `Every ${s.every} hours${at}`;
    }
    case "daily":
      return `Daily at ${pad2(s.hour)}:${pad2(s.minute)}`;
    case "weekly":
      return `Every ${DAY_NAMES[s.dayOfWeek]} at ${pad2(s.hour)}:${pad2(s.minute)}`;
  }
}
