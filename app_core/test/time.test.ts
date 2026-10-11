// Every way of saying a time: the model fills the tool, resolveTime does the arithmetic. Fixed "now" = Sunday 11 Oct 2026, 20:00.
import { resolveTime, fromIso } from "../src/schema.ts";
let pass = 0, fail = 0;
const NOW = fromIso("2026-10-11T20:00");
const t = (name: string, a: any, want: string | null, reported: string | null = null) => {
  const got = resolveTime(NOW, a, reported);
  if (got === want) pass++; else { fail++; console.log("FAIL", name, "got", got, "want", want); }
};
t("8:30 pm today", { hour: 8, minute: 30, period: "pm" }, "2026-10-10T20:30");   // 20:30 has not happened yet at 20:00 -> the most recent 8:30 pm
t("7:30 pm", { hour: 7, minute: 30, period: "pm" }, "2026-10-11T19:30");
t("8:50 am", { hour: 8, minute: 50, period: "am" }, "2026-10-11T08:50");
t("8:39 pm yesterday", { hour: 8, minute: 39, period: "pm", days_ago: 1 }, "2026-10-10T20:39");
t("8:39 pm yesterday (day word)", { hour: 8, minute: 39, period: "pm", day: "yesterday" }, "2026-10-10T20:39");
t("two hours ago", { minutes_ago: 120 }, "2026-10-11T18:00");
t("ten minutes ago", { minutes_ago: 10 }, "2026-10-11T19:50");
t("an hour and a half ago", { minutes_ago: 90 }, "2026-10-11T18:30");
t("two hours later than reported", { minutes_after: 120 }, "2026-10-10T22:35", "2026-10-10T20:35");
t("two hours before reported", { minutes_before: 120 }, "2026-10-10T18:35", "2026-10-10T20:35");
t("an hour later, nothing reported yet", { minutes_after: 60 }, null);
t("day before yesterday", { days_ago: 2 }, "2026-10-09T20:00");
t("three days ago at 3 pm", { hour: 3, minute: 0, period: "pm", days_ago: 3 }, "2026-10-08T15:00");
t("24 hour clock", { hour: 14, minute: 5 }, "2026-10-11T14:05");
t("clock string", { clock: "09:15" }, "2026-10-11T09:15");
t("just now", { just_now: true }, "2026-10-11T20:00");
t("nothing said", {}, null);
t("noon", { hour: 12, minute: 0, period: "pm" }, "2026-10-11T12:00");
t("midnight", { hour: 12, minute: 0, period: "am" }, "2026-10-11T00:00");
t("no am/pm: 9:15 takes the latest that has happened", { hour: 9, minute: 15 }, "2026-10-11T09:15");
console.log(`time: ${pass} passed, ${fail} failed`); process.exit(fail ? 1 : 0);
