/* Jobby — The Overview tab: stats, goals, streak, charts and reminders.
   ES module, no build step. Open the dashboard through server.py. */

import { $, CLOSED, DAY_NAMES, DEFAULT_GOALS, ICON, STATUSES, dayDiff, daysSince, dueLabel, esc, fmtDate, fmtShortDate, isOverdue, isoOf, needsReview, sentByDay, startOfDay, startOfWeek, state, toast, todayISO } from "./core.js";
import { appsIn, computeStats, weeklyBuckets } from "./data.js";


export function renderOverview() {
  const stats = computeStats();
  const cards = [
    {
      label: "Total",
      value: stats.total,
      foot: stats.total ? "Sent since " + fmtDate(oldestApplicationDate()) : "No applications sent yet",
      tone: "var(--accent)",
    },
    {
      label: "Active",
      value: stats.active,
      foot: "Applied, screening or interview",
      tone: "var(--st-applied)",
    },
    {
      label: "Interviews",
      value: stats.interviews,
      foot: stats.offers ? "Includes " + stats.offers + " offer" + (stats.offers === 1 ? "" : "s") : "Reached interview stage",
      tone: "var(--st-interview)",
    },
    {
      label: "Offers",
      value: stats.offers,
      foot: stats.onList ? Math.round((stats.offers / stats.onList) * 100) + "% of applications" : "Keep going",
      tone: "var(--st-offer)",
    },
    {
      label: "Response rate",
      value: stats.responseRate + "<small>%</small>",
      foot: stats.replied + " of " + stats.onList + " got a reply",
      tone: "var(--st-screening)",
    },
    {
      label: "To apply",
      value: stats.toApply,
      foot: stats.toApply ? "Saved, not sent yet" : "No saved jobs",
      tone: "var(--st-withdrawn)",
    },
  ];
  $("#stat-grid").innerHTML = cards
    .map(
      (card) =>
        '<article class="stat-card" style="--tone:' + card.tone + '">' +
        '<div class="stat-label">' + esc(card.label) + "</div>" +
        '<div class="stat-value">' + card.value + "</div>" +
        '<div class="stat-foot">' + esc(card.foot) + "</div>" +
        "</article>"
    )
    .join("");

  const parts = [];
  parts.push(stats.active + " active application" + (stats.active === 1 ? "" : "s"));
  if (stats.followUps) parts.push(stats.followUps + " follow-up" + (stats.followUps === 1 ? "" : "s") + " due");
  if (stats.reviewCount) parts.push(stats.reviewCount + " need" + (stats.reviewCount === 1 ? "s" : "") + " review");
  if (stats.toApply) parts.push(stats.toApply + " saved to apply");
  if (!stats.total && !stats.onList && !stats.toApply) parts.length = 0;
  $("#overview-sub").textContent =
    parts.length ? parts.join(" · ") : "Add your first job to get started.";

  const max = Math.max(1, ...STATUSES.map((status) => stats.byStatus[status.key] || 0));
  $("#funnel").innerHTML = STATUSES.map((status) => {
    const count = stats.byStatus[status.key] || 0;
    const width = count ? Math.max(6, (count / max) * 100) : 0;
    return (
      '<div class="funnel-row" data-status="' + status.key + '">' +
      '<span class="funnel-name">' + status.label + "</span>" +
      '<span class="funnel-track"><span class="funnel-fill" style="width:' + width + '%"></span></span>' +
      '<span class="funnel-count">' + count + "</span>" +
      "</div>"
    );
  }).join("");

  renderGoals(stats);
  renderStreak(stats);
  renderWeeklyChart();
  renderFollowUps();
  renderReviewList();
}


export function goalRing(key, label, count, target, foot) {
  const radius = 34;
  const circumference = 2 * Math.PI * radius;
  const ratio = Math.min(1, count / target);
  const offset = circumference * (1 - ratio);
  const complete = count >= target;
  return (
    '<div class="goal-block" data-goal="' + key + '">' +
    '<div class="goal-ring' + (complete ? " is-complete" : "") + '" role="img"' +
    ' aria-label="' + esc(label + ": " + count + " of " + target) + '">' +
    '<svg viewBox="0 0 84 84" aria-hidden="true">' +
    '<circle class="ring-track" cx="42" cy="42" r="' + radius + '"></circle>' +
    '<circle class="ring-fill" cx="42" cy="42" r="' + radius + '"' +
    ' style="stroke-dasharray:' + circumference.toFixed(2) + ";stroke-dashoffset:" + offset.toFixed(2) + '"></circle>' +
    "</svg>" +
    '<span class="goal-ring-value">' + count + "<small>/" + target + "</small></span>" +
    "</div>" +
    '<div class="goal-ring-label">' + esc(label) + "</div>" +
    '<div class="goal-foot">' + esc(foot) + "</div>" +
    "</div>"
  );
}


export function confettiBurst() {
  if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) return;
  const layer = document.createElement("div");
  layer.className = "confetti-layer";
  layer.setAttribute("aria-hidden", "true");
  const anchor = $("#goal-list").getBoundingClientRect();
  const originX = anchor.width ? anchor.left + anchor.width / 2 : window.innerWidth / 2;
  const originY = anchor.height ? anchor.top + anchor.height * 0.4 : window.innerHeight / 3;
  const colors = ["var(--accent)", "var(--st-offer)", "var(--st-interview)", "#f2b544", "#ef6f9b"];
  for (let index = 0; index < 18; index += 1) {
    const piece = document.createElement("span");
    piece.className = "confetti-piece";
    piece.style.left = originX + (Math.random() * 150 - 75) + "px";
    piece.style.top = originY + (Math.random() * 26 - 13) + "px";
    piece.style.background = colors[index % colors.length];
    piece.style.setProperty("--dx", Math.round(Math.random() * 180 - 90) + "px");
    piece.style.setProperty("--rot", Math.round(Math.random() * 540 - 270) + "deg");
    piece.style.animationDelay = (Math.random() * 0.12).toFixed(2) + "s";
    layer.appendChild(piece);
  }
  document.body.appendChild(layer);
  window.setTimeout(() => layer.remove(), 1900);
}


export function celebrateGoal(stats) {
  if (state.loading) return;
  const today = todayISO();
  const weekKey = isoOf(startOfWeek(new Date()));
  const dailyGoal = Math.max(1, Number(state.settings.daily_goal) || DEFAULT_GOALS.daily_goal);
  const weeklyGoal = Math.max(1, Number(state.settings.weekly_goal) || DEFAULT_GOALS.weekly_goal);
  const previous = state.goalProgress;
  const next = { dayKey: today, weekKey, daily: stats.todayCount, weekly: stats.weekCount };
  state.goalProgress = next;
  if (!previous || previous.dayKey !== today || previous.weekKey !== weekKey) return;
  const dailyHit = previous.daily < dailyGoal && next.daily >= dailyGoal;
  const weeklyHit = previous.weekly < weeklyGoal && next.weekly >= weeklyGoal;
  if (!dailyHit && !weeklyHit) return;
  confettiBurst();
  if (dailyHit) {
    toast("Daily goal complete — " + next.daily + " of " + dailyGoal + " today", false, "celebration");
  }
  if (weeklyHit) {
    toast("Weekly goal complete — " + next.weekly + " of " + weeklyGoal + " this week", false, "celebration");
  }
}


export function renderGoals(stats) {
  celebrateGoal(stats);
  const dailyGoal = Math.max(1, Number(state.settings.daily_goal) || DEFAULT_GOALS.daily_goal);
  const weeklyGoal = Math.max(1, Number(state.settings.weekly_goal) || DEFAULT_GOALS.weekly_goal);
  const dailyLeft = Math.max(0, dailyGoal - stats.todayCount);
  const weeklyLeft = Math.max(0, weeklyGoal - stats.weekCount);
  const daysLeft = 7 - ((new Date().getDay() + 6) % 7);
  const dailyFoot = dailyLeft
    ? dailyLeft + " more today to stay on pace"
    : "Daily goal met — nice work";
  const weeklyFoot = weeklyLeft
    ? weeklyLeft + " more this week · " + daysLeft + " day" + (daysLeft === 1 ? "" : "s") + " left"
    : "Weekly goal met — anything else is a bonus";
  $("#goal-list").innerHTML =
    goalRing("daily_goal", "Daily", stats.todayCount, dailyGoal, dailyFoot) +
    goalRing("weekly_goal", "Weekly", stats.weekCount, weeklyGoal, weeklyFoot);
}


export function renderStreak(stats) {
  const days = stats.days;
  const week = [];
  for (let offset = 6; offset >= 0; offset -= 1) {
    const date = startOfDay(new Date());
    date.setDate(date.getDate() - offset);
    week.push({ date, done: days.has(isoOf(date)) });
  }
  const dots = week
    .map((day) =>
      '<span class="streak-day' + (day.done ? " is-done" : "") +
      (isoOf(day.date) === todayISO() ? " is-today" : "") +
      '" title="' + esc(fmtDate(isoOf(day.date)) + (day.done ? " — applied" : " — nothing sent")) + '">' +
      '<span class="streak-dot"></span>' +
      '<span class="streak-dow">' + DAY_NAMES[day.date.getDay()] + "</span></span>"
    )
    .join("");
  const headline = stats.streak
    ? stats.streak + '<small>day' + (stats.streak === 1 ? "" : "s") + " in a row</small>"
    : 'No streak<small>send one today to start it</small>';
  $("#streak-body").innerHTML =
    '<div class="streak-main">' +
    '<span class="streak-flame' + (stats.streak ? " is-hot" : "") + '" aria-hidden="true">' + ICON.flame + "</span>" +
    '<span class="streak-value">' + headline + "</span>" +
    "</div>" +
    '<div class="streak-week">' + dots + "</div>" +
    '<div class="streak-foot">Best run: <strong>' + stats.bestStreak + " day" + (stats.bestStreak === 1 ? "" : "s") + "</strong>" +
    (stats.streak && stats.streak === stats.bestStreak ? " · at your best right now" : "") +
    "</div>";
}


export function oldestApplicationDate() {
  const days = Array.from(sentByDay().keys()).sort();
  return days[0] || todayISO();
}


export function renderWeeklyChart() {
  const weeks = weeklyBuckets();
  const max = Math.max(1, ...weeks.map((week) => week.count));
  const bars = weeks
    .map((week) => {
      const height = week.count ? Math.max(8, (week.count / max) * 100) : 2;
      return (
        '<div class="chart-bar-wrap" title="' +
        esc(fmtShortDate(week.start) + "–" + fmtShortDate(week.end) + ": " + week.count + " application(s)") +
        '">' +
        '<div class="chart-bar' + (week.count ? "" : " is-zero") + '" style="height:' + height + '%"></div>' +
        "</div>"
      );
    })
    .join("");
  const labels = weeks
    .map((week) => '<div class="chart-label">' + esc(fmtShortDate(week.start)) + "</div>")
    .join("");
  const total = weeks.reduce((sum, week) => sum + week.count, 0);
  const average = (total / weeks.length).toFixed(1).replace(/\.0$/, "");
  $("#weekly-chart").innerHTML =
    '<div class="chart-bars">' + bars + "</div>" +
    '<div class="chart-labels">' + labels + "</div>" +
    '<div class="chart-caption"><span><strong>' + total + "</strong> sent in 8 weeks</span><span>" + average + "/week avg</span></div>";
}


export function renderFollowUps() {
  const list = appsIn("applied")
    .filter((app) => app.next_step_on && !CLOSED.includes(app.status))
    .filter((app) => {
      const diff = dayDiff(app.next_step_on);
      return diff != null && diff <= 7;
    })
    .sort((a, b) => (a.next_step_on < b.next_step_on ? -1 : 1));

  const container = $("#follow-list");
  if (!list.length) {
    container.innerHTML = '<li class="follow-empty">Nothing scheduled. Add a next step to a role you are waiting on.</li>';
    return;
  }
  container.innerHTML = list
    .map((app) => {
      const overdue = isOverdue(app.next_step_on);
      const dueClass = overdue ? "is-overdue" : dayDiff(app.next_step_on) <= 1 ? "is-soon" : "";
      return (
        '<li><button class="follow-item" data-open-app="' + app.id + '">' +
        '<span class="follow-main">' +
        '<span class="follow-company">' + esc(app.company) + "</span>" +
        '<span class="follow-step">' + esc(app.next_step || "Follow up") + " · " + esc(app.role) + "</span>" +
        "</span>" +
        '<span class="follow-due ' + dueClass + '">' + esc(dueLabel(app.next_step_on)) + "</span>" +
        "</button></li>"
      );
    })
    .join("");
}


export function renderReviewList() {
  const list = appsIn("applied")
    .filter(needsReview)
    .sort((a, b) => daysSince(b.applied_on) - daysSince(a.applied_on));
  const container = $("#review-list");
  if (!list.length) {
    container.innerHTML =
      '<li class="follow-empty">Nothing to review. Applications older than 30 days show up here.</li>';
    return;
  }
  container.innerHTML = list
    .map((app) => {
      const days = daysSince(app.applied_on);
      return (
        '<li><button class="follow-item" data-open-app="' + app.id + '">' +
        '<span class="follow-main">' +
        '<span class="follow-company">' + esc(app.company) + "</span>" +
        '<span class="follow-step">' + esc(app.role) + " · applied " + days + " days ago</span>" +
        "</span>" +
        '<span class="follow-due is-review">' + days + "d</span>" +
        "</button></li>"
      );
    })
    .join("");
}
