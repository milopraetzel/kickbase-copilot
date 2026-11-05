"""Anthrazit-Design und Interaktionen der Kickbase-Arbeitsoberfläche."""

CSS = r"""
:root {
  color-scheme: dark;
  --bg: #111111;
  --sidebar: #0d0d0d;
  --surface: #181818;
  --subtle: #141414;
  --hover: #222222;
  --line: #2a2a2a;
  --ink: #f2f2f0;
  --muted: #9e9e9a;
  --faint: #6e6e6a;
  --accent: #f2f2f0;
  --chart: #e2e2de;
  --chart-soft: #1f1f1f;
  --positive: #8fd1ae;
  --warn: #e5be77;
  --warn-bg: #221d14;
  --bad: #f09898;
  --bad-bg: #241616;
  --ring: #d4d4d0;
  --r: 10px;
  font-family: Geist, Arial, Helvetica, sans-serif;
  font-synthesis: none;
}
html {
  scrollbar-gutter: stable;
}
* {
  box-sizing: border-box;
}
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-size: 15px;
  line-height: 1.55;
  -webkit-font-smoothing: antialiased;
}
button,
input,
select {
  font: inherit;
}
button,
a,
input,
select {
  -webkit-tap-highlight-color: transparent;
}
button,
a {
  touch-action: manipulation;
}
a {
  color: inherit;
  text-decoration: none;
}
button {
  cursor: pointer;
}
button:disabled {
  cursor: not-allowed;
  opacity: 0.45;
}
button:focus-visible,
a:focus-visible,
input:focus-visible,
select:focus-visible,
[tabindex]:focus-visible {
  outline: 2px solid var(--ring);
  outline-offset: 4px;
}
input,
select {
  color: var(--ink);
  background: var(--subtle);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px;
  min-height: 42px;
  max-width: 100%;
}
input::placeholder {
  color: var(--faint);
}
svg {
  display: block;
  overflow: visible;
}
h1,
h2,
h3,
p {
  margin: 0;
}
h1 {
  font-size: 34px;
  line-height: 1.15;
  letter-spacing: -1.1px;
  font-weight: 500;
}
h2 {
  font-size: 19px;
  letter-spacing: -0.35px;
  font-weight: 500;
  line-height: 1.35;
}
h3 {
  font-size: 15px;
  font-weight: 500;
}
small {
  font-size: 12px;
}
.skip {
  position: fixed;
  top: -80px;
  left: 20px;
  padding: 12px;
  background: var(--accent);
  color: var(--bg);
  z-index: 100;
}
.skip:focus {
  top: 10px;
}
.workspace {
  display: grid;
  grid-template-columns: 232px minmax(0, 1fr);
  min-height: 100vh;
}
.sidebar {
  position: sticky;
  top: 0;
  height: 100vh;
  padding: 28px 16px 22px;
  border-right: 1px solid var(--line);
  background: var(--sidebar);
  display: flex;
  flex-direction: column;
}
.brand {
  display: grid;
  grid-template-columns: 34px 1fr;
  column-gap: 10px;
  align-items: center;
  padding: 0 8px;
  font-size: 19px;
  font-weight: 500;
  letter-spacing: -0.5px;
}
.brand-mark {
  grid-row: 1/3;
  width: 34px;
  height: 34px;
  display: grid;
  place-items: center;
  border: 1px solid #747474;
  border-radius: 10px;
  font-size: 25px;
  letter-spacing: -2px;
  line-height: 1;
}
.brand-caption {
  font-size: 9px;
  letter-spacing: 2.2px;
  color: var(--faint);
  line-height: 1.5;
}
.league-id {
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 15px 12px;
  margin: 34px 0 30px;
  display: grid;
  gap: 5px;
}
.eyebrow {
  font-size: 11px;
  text-transform: uppercase;
  letter-spacing: 1.1px;
  color: var(--muted);
  font-weight: 500;
}
.league-id strong {
  font-size: 15px;
  font-weight: 500;
  display: flex;
  justify-content: space-between;
}
.league-id strong span {
  font-weight: 400;
  font-size: 12px;
  color: var(--muted);
}
.league-id small {
  font-size: 11px;
  color: var(--faint);
}
.main-nav {
  display: grid;
  gap: 6px;
}
.main-nav button {
  display: flex;
  align-items: center;
  gap: 12px;
  background: transparent;
  border: 0;
  color: var(--muted);
  text-align: left;
  padding: 12px;
  border-radius: 9px;
  min-height: 46px;
}
.main-nav button:hover {
  background: var(--subtle);
}
.main-nav button.active {
  background: var(--line);
  color: var(--ink);
}
.nav-icon {
  font-size: 20px;
  line-height: 1;
  width: 20px;
  text-align: center;
  font-weight: 400;
}
.sidebar-bottom {
  margin-top: auto;
  padding: 20px 8px 0;
}
.sidebar-bottom > a {
  display: flex;
  justify-content: space-between;
  font-size: 13px;
}
.sidebar-bottom > a + a {
  margin-top: 10px;
}
.sidebar-bottom > p {
  font-size: 11px;
  color: var(--faint);
  margin: 6px 0 24px;
}
.manager-avatar {
  display: flex;
  align-items: center;
  gap: 10px;
  padding-top: 20px;
  border-top: 1px solid var(--line);
}
.manager-avatar > span {
  height: 34px;
  width: 34px;
  display: grid;
  place-items: center;
  border-radius: 50%;
  background: var(--hover);
  font-size: 17px;
}
.manager-avatar strong,
.manager-avatar small {
  display: block;
  font-size: 12px;
  font-weight: 500;
}
.manager-avatar small {
  color: var(--faint);
  font-size: 11px;
}
.main-wrap {
  min-width: 0;
  max-width: 1640px;
  width: 100%;
  margin: auto;
}
.topbar {
  height: 77px;
  margin: 0 40px;
  border-bottom: 1px solid var(--line);
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 14px;
  color: var(--muted);
  font-size: 12px;
}
.topbar #breadcrumb > span {
  color: var(--faint);
  margin: 0 12px;
}
.topbar-right {
  display: flex;
  align-items: center;
  gap: 16px;
}
.snapshot {
  font-size: 11px;
}
.snapshot i {
  display: inline-block;
  width: 5px;
  height: 5px;
  background: var(--muted);
  border-radius: 50%;
  margin-right: 7px;
}
.icon-button {
  display: grid;
  place-items: center;
  width: 35px;
  height: 35px;
  font-size: 21px;
  color: var(--muted);
  border: 1px solid var(--line);
  border-radius: 8px;
  background: transparent;
}
.icon-button:hover {
  background: var(--hover);
  color: var(--ink);
}
main {
  padding: 34px 40px 48px;
  min-height: calc(100vh - 150px);
}
.page-title {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 24px;
  margin-bottom: 28px;
}
.page-title p {
  margin-top: 11px;
  color: var(--muted);
  font-size: 14px;
  max-width: 720px;
}
.page-title .eyebrow {
  display: block;
  margin-bottom: 10px;
}
.tag {
  border: 1px solid var(--line);
  border-radius: 5px;
  font-size: 11px;
  display: inline-flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  white-space: nowrap;
  color: var(--muted);
  font-weight: 400;
}
.tag.warn {
  border-color: #4a3d24;
  color: var(--warn);
  background: var(--warn-bg);
}
.tag.blue {
  background: var(--subtle);
  border-color: #444;
  color: var(--ink);
}
.tag.good {
  border-color: #284736;
  background: #142019;
  color: var(--positive);
}
.tag.bad {
  color: var(--bad);
  background: var(--bad-bg);
  border-color: #4f2c2c;
}
.metrics {
  display: grid;
  grid-template-columns: repeat(4, minmax(0, 1fr));
  padding: 24px 0 26px;
  border-top: 1px solid var(--line);
  border-bottom: 1px solid var(--line);
  margin: 0 0 30px;
  gap: 0;
}
.metric {
  padding: 0 24px;
  border-right: 1px solid var(--line);
  min-width: 0;
}
.metric:first-child {
  padding-left: 0;
}
.metric:last-child {
  border-right: 0;
}
.metric > span {
  font-size: 12px;
  color: var(--muted);
}
.metric strong {
  font-size: 31px;
  font-weight: 500;
  letter-spacing: -1px;
  display: block;
  line-height: 1.3;
  margin: 8px 0 5px;
  font-variant-numeric: tabular-nums;
}
.metric strong .unit {
  font-size: 16px;
  font-weight: 400;
  color: var(--muted);
}
.metric small {
  color: var(--faint);
  display: block;
  font-size: 11px;
  line-height: 1.45;
}
.negative {
  color: var(--bad) !important;
}
.positive {
  color: var(--positive) !important;
}
.warning {
  color: var(--warn) !important;
}
.grid-main {
  display: grid;
  grid-template-columns: minmax(0, 1.65fr) minmax(0, 1fr);
  gap: 22px;
  align-items: start;
}
.grid-main > * {
  min-width: 0;
}
.panel {
  background: var(--surface);
  border: 1px solid var(--line);
  border-radius: var(--r);
  padding: 25px;
  min-width: 0;
  margin-bottom: 22px;
}
.panel.quiet {
  background: transparent;
}
.panel-head {
  display: flex;
  justify-content: space-between;
  gap: 18px;
  align-items: flex-start;
  margin-bottom: 24px;
}
.panel-head p {
  color: var(--muted);
  font-size: 12px;
  margin-top: 6px;
  line-height: 1.6;
}
.panel .eyebrow {
  margin-bottom: 8px;
  display: block;
}
.insight-lead {
  font-size: 22px;
  font-weight: 400;
  letter-spacing: -0.6px;
  line-height: 1.45;
  max-width: 610px;
}
.explanation {
  font-size: 13px;
  color: var(--muted);
  line-height: 1.75;
  margin-top: 14px;
  max-width: 710px;
}
.text-link {
  display: inline-flex;
  align-items: center;
  gap: 10px;
  font-size: 12px;
  background: none;
  border: 0;
  color: var(--ink);
  padding: 0;
  min-height: 35px;
}
.text-link:hover {
  color: var(--chart);
}
.button {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 8px;
  min-height: 40px;
  padding: 9px 14px;
  background: var(--accent);
  color: var(--bg);
  border: 1px solid var(--accent);
  border-radius: 8px;
  font-size: 12px;
  font-weight: 500;
  white-space: nowrap;
}
.button:hover {
  opacity: 0.9;
}
.button.secondary {
  color: var(--ink);
  background: transparent;
  border-color: var(--line);
}
.rule-line {
  height: 1px;
  background: var(--line);
  margin: 22px 0;
}
.evidence {
  color: var(--faint);
  font-size: 11px;
  line-height: 1.65;
  margin-top: 18px;
}
.evidence a {
  color: var(--muted);
  text-decoration: underline;
  text-underline-offset: 3px;
}
.evidence details {
  margin-top: 8px;
}
.evidence summary {
  cursor: pointer;
  color: var(--muted);
}
.insight-item {
  display: grid;
  grid-template-columns: 27px minmax(0, 1fr);
  gap: 12px;
  border-top: 1px solid var(--line);
  padding: 19px 0;
}
.insight-item:last-child {
  padding-bottom: 0;
}
.insight-index {
  color: var(--faint);
  font-size: 12px;
  padding-top: 2px;
  font-variant-numeric: tabular-nums;
}
.insight-item p {
  font-size: 12px;
  line-height: 1.7;
  color: var(--muted);
  margin-top: 6px;
}
.budget-list {
  display: grid;
  gap: 16px;
}
.budget-row {
  display: grid;
  grid-template-columns: 130px minmax(20px, 1fr) 88px;
  gap: 12px;
  align-items: center;
  font-size: 12px;
}
.budget-row strong {
  font-weight: 500;
  text-align: right;
  font-variant-numeric: tabular-nums;
}
.budget-track {
  height: 7px;
  border-radius: 3px;
  background: var(--subtle);
  overflow: hidden;
}
.budget-fill {
  height: 100%;
  border-radius: 3px;
  background: var(--chart);
}
.budget-row:last-child {
  padding-top: 18px;
  border-top: 1px solid var(--line);
}
.budget-row:last-child .budget-fill {
  background: var(--ink);
}
.budget-fill.danger {
  background: var(--bad);
}
.budget-fill.muted {
  background: var(--faint);
}
.news-item {
  padding: 16px 0;
  border-top: 1px solid var(--line);
}
.news-item:first-child {
  padding-top: 0;
  border-top: 0;
}
.news-item > a,
.news-item > strong {
  font-size: 13px;
  font-weight: 400;
  line-height: 1.65;
  display: block;
}
.news-item > a:hover {
  color: var(--chart);
}
.news-meta {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  font-size: 10px;
  color: var(--faint);
  margin-top: 8px;
}
.toolbar {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  flex-wrap: wrap;
  margin-bottom: 20px;
}
.toolbar input {
  min-width: 200px;
  flex: 1;
}
.toolbar select {
  font-size: 12px;
  min-width: 140px;
}
.segmented {
  display: inline-flex;
  align-items: center;
  gap: 2px;
  padding: 3px;
  background: var(--subtle);
  border: 1px solid var(--line);
  border-radius: 8px;
}
.segmented button {
  background: transparent;
  border: 0;
  color: var(--muted);
  font-size: 11px;
  border-radius: 5px;
  padding: 6px 10px;
  min-height: 30px;
}
.segmented button.active {
  background: var(--line);
  color: var(--ink);
}
.table-wrap {
  overflow-x: auto;
}
table {
  width: 100%;
  border-collapse: collapse;
  text-align: left;
  font-size: 12px;
}
th {
  color: var(--faint);
  font-weight: 400;
  font-size: 10px;
  padding: 0 12px 14px;
  white-space: nowrap;
}
th:first-child,
td:first-child {
  padding-left: 0;
}
th:last-child,
td:last-child {
  padding-right: 0;
}
td {
  border-top: 1px solid var(--line);
  padding: 15px 12px;
  white-space: nowrap;
  font-variant-numeric: tabular-nums;
}
tr:last-child td {
  padding-bottom: 3px;
}
td.num,
th.num {
  text-align: right;
}
.player-name {
  display: flex;
  align-items: center;
  gap: 11px;
  background: transparent;
  color: var(--ink);
  border: 0;
  padding: 0;
  text-align: left;
  font-size: 12px;
  min-height: 35px;
}
.player-name:hover strong {
  color: var(--chart);
}
.portrait {
  display: grid;
  place-items: center;
  flex: 0 0 32px;
  width: 32px;
  height: 32px;
  border: 1px solid var(--line);
  border-radius: 50%;
  background: var(--subtle);
  font-size: 10px;
  color: var(--muted);
  overflow: hidden;
}
.portrait img {
  width: 100%;
  height: 100%;
  object-fit: contain;
}
.player-name strong {
  font-weight: 500;
  display: block;
}
.player-name small {
  display: block;
  color: var(--faint);
  font-size: 10px;
  margin-top: 3px;
}
.chart {
  position: relative;
  min-width: 0;
}
.chart svg {
  width: 100%;
  height: auto;
}
.chart .grid-line {
  stroke: var(--line);
  stroke-width: 1;
}
.chart text {
  fill: var(--faint);
  font:
    10px Geist,
    Arial,
    sans-serif;
}
.chart .axis-label {
  fill: var(--muted);
  font-size: 10px;
}
.chart circle[data-tip],
.chart rect[data-tip] {
  cursor: pointer;
}
.chart circle[data-tip]:hover,
.chart circle[data-tip]:focus {
  stroke: var(--ink);
  stroke-width: 2;
}
.chart .zero-line {
  stroke: var(--muted);
  stroke-width: 1;
  stroke-dasharray: 4 5;
}
.chart-legend {
  display: flex;
  flex-wrap: wrap;
  gap: 18px;
  font-size: 10px;
  color: var(--muted);
  margin-top: 10px;
}
.chart-key-button {
  background: none;
  border: 0;
  padding: 0;
  color: var(--muted);
  font-size: 10px;
  display: flex;
  align-items: center;
  gap: 6px;
  min-height: 30px;
}
.chart-key-button i {
  width: 6px;
  height: 6px;
  border-radius: 50%;
  background: var(--chart);
}
.chart-key-button[aria-pressed="false"] {
  opacity: 0.45;
  text-decoration: line-through;
}
.rank-list {
  display: grid;
  gap: 14px;
}
.rank-row {
  display: grid;
  grid-template-columns: 22px 105px minmax(50px, 1fr) 45px;
  gap: 12px;
  align-items: center;
  font-size: 12px;
}
.rank-row .rank-index {
  font-size: 10px;
  color: var(--faint);
}
.rank-label {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.rank-bar {
  height: 6px;
  background: var(--subtle);
  border-radius: 2px;
  overflow: hidden;
}
.rank-bar i {
  height: 100%;
  display: block;
  background: #727272;
  border-radius: 2px;
}
.rank-row.me .rank-bar i {
  background: var(--chart);
}
.rank-row.me {
  color: #b6caff;
}
.rank-row.target {
  padding-bottom: 13px;
  border-bottom: 1px dashed #767676;
}
.rank-row strong {
  font-weight: 500;
  text-align: right;
  font-size: 11px;
}
.empty {
  padding: 26px 0;
  color: var(--muted);
  font-size: 13px;
  line-height: 1.7;
}
.compact-stat {
  font-size: 28px;
  font-weight: 400;
  letter-spacing: -0.8px;
  font-variant-numeric: tabular-nums;
}
.compact-label {
  font-size: 11px;
  color: var(--muted);
  margin-top: 5px;
}
.two-stats {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 24px;
  margin-bottom: 22px;
}
.two-stats > div + div {
  border-left: 1px solid var(--line);
  padding-left: 24px;
}
.learning-row {
  display: grid;
  grid-template-columns: 27px minmax(0, 1fr);
  gap: 12px;
  padding: 17px 0;
  border-top: 1px solid var(--line);
  align-items: start;
}
.learning-row > span {
  color: var(--faint);
  font-size: 11px;
}
.learning-row p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 5px;
}
.calc-grid {
  display: grid;
  grid-template-columns: minmax(0, 1.4fr) minmax(0, 1fr);
  gap: 28px;
}
.calc-grid > * {
  min-width: 0;
}
.calc-control {
  display: flex;
  gap: 8px;
  margin-bottom: 16px;
}
.calc-control select {
  flex: 1;
  min-width: 0;
  font-size: 12px;
}
.calc-items {
  display: grid;
  gap: 10px;
}
.calc-item {
  display: grid;
  grid-template-columns: minmax(0, 1fr) 125px 28px;
  gap: 10px;
  align-items: center;
  font-size: 12px;
}
.calc-item input {
  width: 100%;
  font-size: 12px;
  min-height: 36px;
  padding: 8px;
}
.calc-item button {
  border: 0;
  background: none;
  color: var(--muted);
  font-size: 20px;
}
.calc-note {
  font-size: 11px;
  color: var(--faint);
  margin-top: 15px;
}
.calc-summary {
  background: var(--subtle);
  border: 1px solid var(--line);
  border-radius: 10px;
  padding: 21px;
}
.calc-summary label {
  display: block;
  font-size: 11px;
  color: var(--muted);
}
.calc-summary input {
  width: 100%;
  margin: 8px 0 14px;
  font-variant-numeric: tabular-nums;
}
.calc-total {
  font-size: 27px;
  font-weight: 500;
  letter-spacing: -0.8px;
}
.calc-result {
  margin-top: 11px;
  font-size: 12px;
  line-height: 1.65;
}
.drawer-top {
  display: flex;
  align-items: center;
  justify-content: space-between;
  color: var(--muted);
  font-size: 12px;
  border-bottom: 1px solid var(--line);
  padding: 17px 24px;
  position: sticky;
  top: 0;
  z-index: 1;
  background: var(--sidebar);
}
dialog {
  padding: 0;
  position: fixed;
  margin: 0 0 0 auto;
  inset: 0 0 0 auto;
  width: min(640px, 100%);
  height: 100dvh;
  max-width: 100%;
  max-height: 100%;
  border: 0;
  border-left: 1px solid var(--line);
  color: var(--ink);
  background: var(--bg);
  box-shadow: -25px 0 80px #0003;
}
dialog:modal {
  overscroll-behavior: contain;
}
dialog::backdrop {
  background: rgba(0, 0, 0, 0.62);
  backdrop-filter: blur(3px);
  overflow: hidden;
  overscroll-behavior: contain;
}
#drawer-content {
  padding: 30px 27px 50px;
}
.drawer-heading {
  display: flex;
  align-items: center;
  gap: 15px;
  margin-bottom: 26px;
}
.drawer-heading .portrait {
  width: 55px;
  height: 55px;
  flex-basis: 55px;
  font-size: 15px;
}
.drawer-heading h2 {
  font-size: 27px;
}
.drawer-heading p {
  font-size: 12px;
  color: var(--muted);
  margin-top: 5px;
}
.detail-metrics {
  display: grid;
  grid-template-columns: 1fr 1fr;
  gap: 22px;
  margin-bottom: 30px;
}
.detail-metrics > div {
  border-top: 1px solid var(--line);
  padding-top: 15px;
}
.detail-metrics strong {
  font-size: 24px;
  font-weight: 400;
  letter-spacing: -0.5px;
  display: block;
}
.detail-metrics span {
  font-size: 11px;
  color: var(--muted);
  display: block;
  margin-top: 5px;
}
.detail-block {
  margin-top: 26px;
  padding-top: 24px;
  border-top: 1px solid var(--line);
}
.detail-block h3 {
  margin-bottom: 12px;
}
.detail-block p {
  font-size: 12px;
  line-height: 1.75;
  color: var(--muted);
}
.fixtures {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
}
.fixture {
  background: var(--surface);
  border-radius: 8px;
  padding: 10px 12px;
  flex: 1;
  min-width: 100px;
  font-size: 11px;
}
.fixture strong {
  display: block;
  font-weight: 500;
  margin-bottom: 4px;
}
.fixture small {
  color: var(--faint);
  font-size: 10px;
}
#tooltip {
  position: fixed;
  z-index: 200;
  max-width: 260px;
  padding: 11px 13px;
  border-radius: 8px;
  border: 1px solid #666;
  background: #1b1b1b;
  color: var(--ink);
  font-size: 11px;
  white-space: pre-line;
  pointer-events: none;
  box-shadow: 0 8px 25px #0005;
}
footer {
  display: flex;
  justify-content: space-between;
  gap: 20px;
  padding: 0 40px 27px;
  color: var(--faint);
  font-size: 10px;
}
.mini-dots {
  display: flex;
  gap: 4px;
  margin: 16px 0 10px;
}
.mini-dots i {
  height: 8px;
  flex: 1;
  background: var(--line);
  border-radius: 2px;
}
.mini-dots i.filled {
  background: #a8bbeb;
}
.count-row {
  display: flex;
  justify-content: space-between;
  color: var(--muted);
  font-size: 11px;
}
.source-row {
  padding: 14px 0;
  border-top: 1px solid var(--line);
  font-size: 12px;
}
.source-row strong {
  font-weight: 500;
}
.source-row p {
  color: var(--muted);
  font-size: 11px;
  margin-top: 4px;
}
.source-row a {
  color: #b6caff;
}
.source-row .tag {
  margin-left: 8px;
}
@media (min-width: 1700px) {
  main {
    padding-left: 52px;
    padding-right: 52px;
  }
  .topbar {
    margin-left: 52px;
    margin-right: 52px;
  }
  footer {
    padding-left: 52px;
    padding-right: 52px;
  }
}
@media (max-width: 1180px) {
  .workspace {
    grid-template-columns: 200px minmax(0, 1fr);
  }
  main {
    padding: 30px 25px 44px;
  }
  .topbar {
    margin: 0 25px;
  }
  .metric {
    padding: 0 15px;
  }
  .metric strong {
    font-size: 27px;
  }
  .grid-main {
    grid-template-columns: minmax(0, 1.35fr) minmax(0, 1fr);
    gap: 16px;
  }
  .panel {
    padding: 20px;
  }
  .rank-row {
    grid-template-columns: 18px 86px minmax(30px, 1fr) 40px;
    gap: 8px;
  }
  .budget-row {
    grid-template-columns: 110px minmax(20px, 1fr) 76px;
    gap: 8px;
  }
  .brand {
    font-size: 17px;
  }
  .calc-grid {
    grid-template-columns: 1fr;
  }
  footer {
    padding-left: 25px;
    padding-right: 25px;
  }
}
@media (max-width: 950px) {
  .grid-main {
    grid-template-columns: 1fr;
  }
  .metrics {
    grid-template-columns: repeat(2, minmax(0, 1fr));
    gap: 24px 0;
  }
  .metric:nth-child(2) {
    border: 0;
  }
  .metric:nth-child(3) {
    padding-left: 0;
  }
  .grid-main > .side-column {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 16px;
  }
  .grid-main > .side-column > .panel {
    margin-bottom: 0;
  }
  .grid-main > .side-column > .panel:last-child:nth-child(odd) {
    grid-column: 1/-1;
  }
  .page-title {
    gap: 15px;
  }
  .page-title > .tag {
    display: none;
  }
  .calc-grid {
    grid-template-columns: minmax(0, 1.3fr) 1fr;
  }
  h1 {
    font-size: 31px;
  }
}
@media (max-width: 720px) {
  .workspace {
    display: block;
  }
  .sidebar {
    position: static;
    width: 100%;
    height: auto;
    padding: 16px 20px;
    border-right: 0;
    border-bottom: 1px solid var(--line);
    display: grid;
    grid-template-columns: 1fr auto;
    gap: 19px;
  }
  .brand {
    padding: 0;
    font-size: 18px;
  }
  .brand-mark {
    width: 31px;
    height: 31px;
  }
  .league-id {
    display: none;
  }
  .sidebar-bottom {
    padding: 0;
    margin: 0;
    grid-column: 2;
    grid-row: 1;
    align-self: center;
  }
  .sidebar-bottom > a {
    font-size: 11px;
    gap: 12px;
  }
  .sidebar-bottom {
    display: flex;
    gap: 14px;
  }
  .sidebar-bottom > a + a {
    margin-top: 0;
  }
  .sidebar-bottom > p,
  .manager-avatar {
    display: none;
  }
  .main-nav {
    grid-column: 1/-1;
    display: flex;
    gap: 5px;
  }
  .main-nav button {
    font-size: 12px;
    justify-content: center;
    gap: 7px;
    flex: 1;
    padding: 9px 8px;
    min-height: 40px;
  }
  .nav-icon {
    font-size: 17px;
    width: 16px;
  }
  .topbar {
    height: 54px;
    margin: 0 20px;
  }
  .snapshot {
    font-size: 10px;
  }
  .topbar #breadcrumb {
    display: none;
  }
  .topbar-right {
    width: 100%;
    justify-content: space-between;
  }
  .topbar .icon-button {
    width: 29px;
    height: 29px;
    font-size: 17px;
  }
  main {
    padding: 26px 20px 30px;
  }
  .page-title {
    margin-bottom: 25px;
  }
  .page-title p {
    font-size: 13px;
    line-height: 1.7;
  }
  .page-title .eyebrow {
    font-size: 10px;
  }
  h1 {
    font-size: 29px;
  }
  .metrics {
    padding-top: 20px;
    padding-bottom: 22px;
    margin-bottom: 24px;
  }
  .metric strong {
    font-size: 27px;
  }
  .metric > span {
    font-size: 11px;
  }
  .metric small {
    font-size: 10px;
  }
  .panel {
    padding: 21px 18px;
    margin-bottom: 18px;
  }
  .panel-head {
    margin-bottom: 21px;
  }
  .panel-head p {
    font-size: 11px;
  }
  .insight-lead {
    font-size: 21px;
  }
  .explanation {
    font-size: 12px;
  }
  .grid-main > .side-column {
    display: block;
  }
  .grid-main > .side-column > .panel {
    margin-bottom: 18px;
  }
  .calc-grid {
    grid-template-columns: 1fr;
    gap: 20px;
  }
  .toolbar input {
    min-width: 160px;
  }
  .toolbar select {
    min-width: 115px;
  }
  .toolbar {
    gap: 8px;
  }
  .budget-row {
    grid-template-columns: 112px minmax(20px, 1fr) 78px;
  }
  .rank-row {
    grid-template-columns: 18px 95px minmax(40px, 1fr) 43px;
  }
  footer {
    display: block;
    padding: 10px 20px 27px;
    font-size: 10px;
  }
  footer span {
    display: block;
    margin-top: 5px;
  }
  .chart text {
    font-size: 11px;
  }
  .segmented button {
    padding: 5px 8px;
  }
  .two-stats {
    gap: 17px;
  }
  .two-stats > div + div {
    padding-left: 17px;
  }
  .compact-stat {
    font-size: 26px;
  }
  .calc-item {
    grid-template-columns: minmax(0, 1fr) 109px 25px;
  }
  .drawer-top {
    padding: 15px 20px;
  }
  #drawer-content {
    padding: 25px 20px 40px;
  }
}
@media (prefers-reduced-motion: no-preference) {
  #content {
    animation: appear 0.22s ease-out;
  }
  @keyframes appear {
    from {
      opacity: 0.5;
      transform: translateY(4px);
    }
    to {
      opacity: 1;
      transform: translateY(0);
    }
  }
}
@media print {
  .sidebar,
  .topbar,
  .toolbar,
  footer,
  .button {
    display: none !important;
  }
  .workspace {
    display: block;
  }
  main {
    padding: 0;
  }
  .panel {
    break-inside: avoid;
  }
  dialog {
    display: none;
  }
}
/* Ein Manager, dieselbe Übersicht. */
.manager-switcher {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 0 20px;
  position: sticky;
  top: 0;
  z-index: 8;
  background: var(--bg);
  border-bottom: 1px solid var(--line);
  margin-bottom: 25px;
}
.manager-switcher > div {
  display: flex;
  align-items: center;
  gap: 15px;
  min-width: 0;
}
.manager-switcher label {
  font-size: 12px;
  color: var(--muted);
}
.manager-switcher select {
  min-width: 235px;
  font-size: 14px;
}
.overview-charts {
  display: grid;
  grid-template-columns: repeat(2, minmax(0, 1fr));
  gap: 22px;
  align-items: start;
}
.overview-charts > .panel {
  margin-bottom: 22px;
}
.compact-chart {
  min-height: 145px;
}
.compact-chart svg {
  max-height: 230px;
}
.cell-sub {
  display: block;
  font-size: 10px;
  margin-top: 5px;
}
.league-managers tr[data-manager] {
  cursor: pointer;
}
.league-managers tr[data-manager]:hover td {
  background: var(--hover);
}
.league-managers .my-manager td {
  background: var(--chart-soft);
}
.league-managers .my-manager td:first-child {
  box-shadow: inset 3px 0 0 var(--chart);
  padding-left: 12px;
}
.league-managers strong {
  font-weight: 500;
}
.floor-control {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 11px;
  color: var(--muted);
  margin-top: 15px;
}
.floor-control input {
  min-height: auto;
}
.action-plan h2 {
  font-size: 25px;
  line-height: 1.35;
  letter-spacing: -0.65px;
}
.action-plan .panel-head > div > p {
  font-size: 13px;
  max-width: 590px;
}
.action-step {
  display: grid;
  grid-template-columns: 35px minmax(0, 1fr);
  gap: 15px;
  padding: 22px 0;
  border-top: 1px solid var(--line);
}
.action-step:last-of-type {
  padding-bottom: 8px;
}
.action-number {
  font-size: 12px;
  font-variant-numeric: tabular-nums;
  color: var(--faint);
  padding-top: 2px;
}
.step-heading {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 15px;
}
.step-heading h3 {
  font-size: 15px;
}
.action-step p {
  font-size: 12px;
  color: var(--muted);
  line-height: 1.75;
  margin-top: 8px;
}
.step-amount {
  display: block;
  font-size: 21px;
  font-weight: 500;
  letter-spacing: -0.5px;
  margin-top: 10px;
}
.step-actions {
  display: flex;
  gap: 22px;
  flex-wrap: wrap;
  margin-top: 8px;
}
.date-metric {
  font-size: 20px;
  line-height: 1.35;
  display: block;
  letter-spacing: -0.5px;
}
.detail-block .detail-metrics {
  margin-top: 20px;
}
.detail-block .detail-metrics strong {
  font-size: 21px;
}
@media (max-width: 1100px) {
  .overview-charts {
    gap: 16px;
  }
  .overview-charts .panel-head {
    flex-wrap: wrap;
    gap: 12px;
  }
  .compact-chart {
    min-height: 120px;
  }
  .manager-switcher select {
    min-width: 210px;
  }
}
@media (max-width: 850px) {
  .overview-charts {
    grid-template-columns: 1fr;
  }
  .overview-charts > .panel {
    margin-bottom: 0;
  }
  .overview-charts {
    margin-bottom: 22px;
  }
  .manager-switcher {
    gap: 10px;
  }
  .manager-switcher select {
    min-width: 190px;
  }
  .step-heading {
    flex-wrap: wrap;
    gap: 8px;
  }
}
@media (max-width: 520px) {
  .manager-switcher > div {
    gap: 8px;
    flex: 1;
  }
  .manager-switcher label {
    display: none;
  }
  .manager-switcher select {
    width: 100%;
    min-width: 0;
  }
  .manager-switcher > .button {
    font-size: 11px;
    padding: 9px 11px;
  }
  .action-step {
    grid-template-columns: 24px minmax(0, 1fr);
    gap: 10px;
  }
  .action-plan h2 {
    font-size: 22px;
  }
  .date-metric {
    font-size: 17px;
  }
  .panel-head .segmented {
    flex-wrap: wrap;
  }
  .step-actions {
    gap: 12px;
  }
}
"""

# Lange HTML-Templates werden ohne inhaltliche Änderung als Literale zusammengesetzt.
CSS += r"""
.result-bridge {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:16px;
  margin:0 0 10px;padding:14px 0;border-top:1px solid var(--line);}
.result-bridge span {display:block;font-size:11px;color:var(--muted);margin-bottom:7px;}
.result-bridge strong {font-size:17px;font-weight:500;font-variant-numeric:tabular-nums;}
.bridge-total {border-left:1px solid var(--line);padding-left:16px;}
@media (max-width:620px) {
  .result-bridge {grid-template-columns:repeat(2,minmax(0,1fr));}
  .bridge-total {border-left:0;padding-left:0;}
}
.rank-row.me {color:var(--ink);font-weight:500;}
.source-row a {color:var(--ink);text-decoration:underline;text-underline-offset:3px;}
.mini-dots i.filled {background:var(--ink);}
.tag.jev {background:var(--subtle);border-color:var(--line);color:var(--muted);}
.tag.jev-good {background:#142019;border-color:#284736;color:var(--positive);}
.tag.jev-bad {background:var(--bad-bg);border-color:#4f2c2c;color:var(--bad);}
.ai-dossier {background:var(--surface);border:1px solid var(--line);
  border-radius:var(--r);padding:18px 20px;margin:18px 0 22px;}
.ai-dossier-head {display:flex;justify-content:space-between;align-items:flex-start;
  gap:12px;margin-bottom:12px;flex-wrap:wrap;}
.ai-dossier-head h3 {font-size:15px;font-weight:500;color:var(--ink);}
.ai-verdict {background:var(--subtle);border:1px solid var(--line);
  border-radius:8px;padding:12px 14px;margin-bottom:12px;}
.ai-verdict.good {border-color:#284736;background:#131d17;}
.ai-verdict.warn {border-color:#4a3d24;background:#1d1912;}
.ai-verdict.bad {border-color:#4f2c2c;background:#1f1414;}
.ai-verdict strong {display:block;font-size:14px;font-weight:500;color:var(--ink);}
.ai-verdict p {font-size:12px;color:var(--muted);margin-top:4px;line-height:1.5;}
.forecast-strip {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;
  margin:12px 0;}
.forecast-cell {background:var(--subtle);border:1px solid var(--line);
  border-radius:8px;padding:10px 11px;min-width:0;}
.forecast-cell span {display:block;font-size:10px;color:var(--muted);
  text-transform:uppercase;letter-spacing:0.6px;}
.forecast-cell strong {display:block;font-size:16px;font-weight:500;margin:4px 0 2px;
  font-variant-numeric:tabular-nums;}
.forecast-cell small {display:block;font-size:10px;color:var(--faint);}
.jev-box {background:var(--subtle);border:1px solid var(--line);
  border-radius:8px;padding:13px 15px;margin-top:12px;}
.jev-box-head {display:flex;justify-content:space-between;align-items:center;
  gap:10px;margin-bottom:10px;flex-wrap:wrap;}
.jev-box-head strong {font-size:12px;letter-spacing:0.2px;color:var(--ink);
  display:flex;align-items:center;gap:8px;font-weight:500;}
.jev-pill {display:inline-block;background:var(--ink);color:var(--bg);font-size:9px;
  font-weight:600;padding:2px 6px;border-radius:3px;letter-spacing:0.6px;}
.jev-item {border-top:1px solid var(--line);padding:10px 0 2px;margin-top:8px;}
.jev-item:first-of-type {border-top:0;padding-top:0;margin-top:0;}
.jev-item-top {display:flex;justify-content:space-between;align-items:center;
  gap:8px;flex-wrap:wrap;margin-bottom:4px;}
.jev-item-top strong {font-size:12px;color:var(--ink);font-weight:500;}
.jev-item p {font-size:12px;color:var(--muted);line-height:1.45;margin:3px 0 6px;}
.prob-chips {display:flex;flex-wrap:wrap;gap:6px;margin:6px 0;}
.prob-chip {font-size:10px;padding:2px 6px;border-radius:4px;background:var(--bg);
  border:1px solid var(--line);color:var(--muted);font-variant-numeric:tabular-nums;}
.prob-chip.top {border-color:var(--ink);color:var(--ink);}
.jev-source {font-size:11px;color:var(--faint);display:flex;justify-content:space-between;
  gap:10px;flex-wrap:wrap;margin-top:4px;}
.jev-source a {color:var(--ink);text-decoration:underline;text-underline-offset:3px;}
.eff-controls {display:grid;gap:12px;margin-bottom:16px;}
.eff-filter-row {display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;
  align-items:center;}
.eff-toolbar {margin-bottom:0;}
.eff-strip {grid-template-columns:repeat(3,minmax(0,1fr));margin:0 0 16px;}
.th-sort {background:none;border:0;color:inherit;font:inherit;padding:0;
  display:inline-flex;align-items:center;gap:4px;}
.th-sort:hover {color:var(--ink);}
.th-sort.active {color:var(--ink);font-weight:600;text-decoration:underline;
  text-underline-offset:4px;}
.eff-active-val {color:var(--ink);font-weight:600;}
.eff-more-wrap {display:flex;justify-content:center;margin-top:14px;}
.eff-table-wrap {overflow:auto;max-inline-size:100%;max-block-size:min(560px,80vh);
  container-type:inline-size;}
.eff-table {border-collapse:separate;border-spacing:0;}
.eff-table thead {position:sticky;inset-block-start:0;z-index:1;background:var(--surface);}
.eff-table tbody th {position:sticky;inset-inline-start:0;background:var(--surface);
  font-weight:400;text-align:left;padding:13px 12px 13px 0;border-top:1px solid var(--line);}
@container (width < 600px) {
  .eff-table, .eff-table thead, .eff-table tbody,
  .eff-table tr, .eff-table th, .eff-table td {display:block;}
  .eff-table thead {display:none;}
  .eff-table tr {margin-block-end:1rem;border:1px solid var(--line);border-radius:8px;
    padding:10px 12px;}
  .eff-table tbody th {position:sticky;inset-block-start:0;z-index:2;border-top:0;
    padding:0 0 8px;}
  .eff-table {
    --label-2: "Verfügbarkeit & Rolle";
    --label-3: "Marktwert";
    --label-4: "Ø Pkt. / Mio. €";
    --label-5: "Gesamt / Mio. €";
    --label-6: "4-ST-Prog. / Mio. €";
  }
  .eff-table td:nth-child(2)::before {content:var(--label-2) ": ";color:var(--muted);}
  .eff-table td:nth-child(3)::before {content:var(--label-3) ": ";color:var(--muted);}
  .eff-table td:nth-child(4)::before {content:var(--label-4) ": ";color:var(--muted);}
  .eff-table td:nth-child(5)::before {content:var(--label-5) ": ";color:var(--muted);}
  .eff-table td:nth-child(6)::before {content:var(--label-6) ": ";color:var(--muted);}
}
@media (max-width:620px) {
  .forecast-strip, .eff-strip {grid-template-columns:repeat(2,minmax(0,1fr));}
}
.topbar {height:56px;position:sticky;top:0;z-index:20;
  background:rgba(17,17,17,0.92);backdrop-filter:blur(8px);}
.btn-sm {min-height:30px;padding:4px 10px;font-size:11px;border-radius:6px;}
.button.danger {background:var(--bad-bg);color:var(--bad);border-color:#4f2c2c;}
.button.danger:hover {background:#301b1b;}
.snapshot.is-live i {background:var(--positive);}
.toast {position:fixed;right:24px;bottom:24px;z-index:250;background:var(--surface);
  border:1px solid var(--line);color:var(--ink);padding:10px 14px;border-radius:8px;
  font-size:12px;box-shadow:0 10px 30px #0009;max-width:420px;}
.toast.good {border-color:#284736;background:#142019;color:var(--positive);}
.toast.bad {border-color:#4f2c2c;background:var(--bad-bg);color:var(--bad);}
.compact-details {padding:14px 20px;}
.compact-details > summary {cursor:pointer;list-style:none;display:flex;
  justify-content:space-between;align-items:center;gap:12px;font-weight:500;font-size:13px;
  color:var(--muted);}
.compact-details > summary:hover {color:var(--ink);}
.compact-details > summary::-webkit-details-marker {display:none;}
.control-hub {background:var(--surface);border:1px solid var(--line);margin-bottom:20px;}
.control-hub .panel-head {margin-bottom:12px;align-items:center;
  flex-wrap:wrap;flex-wrap:balance;}
.hub-actions {display:flex;gap:8px;align-items:center;flex-wrap:wrap;flex-wrap:balance;}
.backend-guide {border-top:1px solid var(--line);padding-top:10px;margin-top:10px;}
.backend-guide > summary {cursor:pointer;font-size:11px;color:var(--muted);font-weight:400;
  display:inline-flex;align-items:center;gap:6px;}
.backend-guide > summary:hover {color:var(--ink);}
.guide-grid {display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:8px;
  margin-top:10px;}
.guide-card {background:var(--subtle);border:1px solid var(--line);border-radius:7px;
  padding:9px 10px;font-size:11px;}
.guide-card strong {display:block;color:var(--ink);margin-bottom:2px;
  font-size:11px;font-weight:500;}
.guide-card code {display:inline-block;font-size:10px;color:var(--muted);
  background:var(--bg);border:1px solid var(--line);padding:1px 5px;
  border-radius:4px;margin-bottom:4px;}
.guide-card span {display:block;color:var(--muted);line-height:1.4;}
.intent-grid {display:grid;gap:8px;margin-top:10px;}
.intent-card {background:var(--subtle);border:1px solid var(--line);
  border-radius:8px;padding:12px 14px;}
.intent-head {display:flex;justify-content:space-between;align-items:center;
  gap:12px;flex-wrap:wrap;flex-wrap:balance;}
.intent-title {display:flex;align-items:center;gap:8px;flex-wrap:wrap;flex-wrap:balance;}
.intent-amount {font-size:16px;font-weight:500;font-variant-numeric:tabular-nums;color:var(--ink);}
.intent-amount small {display:block;font-size:10px;font-weight:400;color:var(--faint);
  text-align:right;}
.intent-reason {font-size:12px;color:var(--muted);margin:6px 0 8px;line-height:1.4;}
.intent-actions {display:flex;gap:6px;align-items:center;flex-wrap:wrap;flex-wrap:balance;
  padding-top:8px;border-top:1px solid var(--line);}
.intent-inline-form {display:flex;gap:6px;align-items:center;flex-wrap:wrap;
  margin-top:8px;padding-top:8px;border-top:1px dashed var(--line);}
.intent-inline-form input {min-height:30px;padding:4px 8px;font-size:12px;width:150px;}
.ai-factor-pill {display:inline-flex;align-items:center;gap:3px;font-size:11px;
  font-weight:500;padding:2px 7px;border-radius:5px;border:1px solid var(--line);
  background:var(--subtle);color:var(--ink);font-variant-numeric:tabular-nums;white-space:nowrap;}
.ai-factor-pill i {font-style:normal;font-weight:400;color:var(--faint);font-size:10px;}
.ai-factor-pill.good {background:#142019;border-color:#284736;color:var(--positive);}
.ai-factor-pill.blue {background:var(--subtle);border-color:var(--line);color:var(--ink);}
.ai-factor-pill.warn {background:var(--warn-bg);border-color:#4a3d24;color:var(--warn);}
.ai-factor-pill.bad {background:var(--bad-bg);border-color:#4f2c2c;color:var(--bad);}
.ai-factor-cell {display:inline-flex;align-items:center;gap:8px;}
.ai-factor-bar {height:4px;width:44px;background:var(--bg);border-radius:2px;overflow:hidden;}
.ai-factor-bar > i {display:block;height:100%;background:var(--ink);border-radius:2px;}
.ai-factor-bar.good > i {background:var(--positive);}
.ai-factor-bar.warn > i {background:var(--warn);}
.ai-factor-bar.bad > i {background:var(--bad);}
.factor-chips {display:flex;gap:4px;flex-wrap:wrap;flex-wrap:balance;}
.factor-chip {font-size:10px;padding:2px 6px;border-radius:4px;
  background:var(--subtle);border:1px solid var(--line);
  color:var(--muted);white-space:nowrap;font-variant-numeric:tabular-nums;}
.ai-pillars {display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px;
  margin:10px 0 12px;}
.ai-pillar {background:var(--subtle);border:1px solid var(--line);
  border-radius:7px;padding:9px 11px;}
.ai-pillar-top {display:flex;justify-content:space-between;align-items:center;
  font-size:11px;margin-bottom:5px;}
.ai-pillar-top span {color:var(--muted);}
.ai-pillar-top strong {font-variant-numeric:tabular-nums;color:var(--ink);font-weight:500;}
.ai-pillar-track {height:4px;background:var(--bg);border-radius:2px;overflow:hidden;
  margin-bottom:5px;}
.ai-pillar-fill {height:100%;background:var(--ink);border-radius:2px;}
.ai-pillar small {display:block;font-size:10px;color:var(--faint);}
.pitch-wrap {max-width:440px;margin:6px auto 0;}
.pitch {display:block;width:100%;height:auto;border-radius:8px;}
.pitch-grass {fill:#0f1812;}
.pitch-stripe {fill:#121d16;opacity:.75;}
.pitch-line {fill:none;stroke:#233329;stroke-width:1.4;}
.pitch-spot {fill:#233329;}
.pp {cursor:pointer;}
.pp-ring {stroke:var(--bg);stroke-width:2;}
.pp-starter .pp-ring {fill:var(--positive);}
.pp-bench .pp-ring {fill:var(--warn);}
.pp-out .pp-ring {fill:var(--bad);}
.pp-unknown .pp-ring {fill:var(--faint);}
.pp-inner {fill:#181818;}
.pp-badge {fill:#0d0d0d;stroke:var(--line);stroke-width:1;}
.pp-score-s {fill:var(--ink);font-size:9.5px;font-weight:600;text-anchor:middle;
  font-variant-numeric:tabular-nums;}
.pp-score {fill:#0d0d0d;font-size:11px;font-weight:600;text-anchor:middle;
  font-variant-numeric:tabular-nums;}
.pp-name {fill:var(--ink);font-size:11.5px;font-weight:500;text-anchor:middle;
  paint-order:stroke;stroke:#0d0d0d;stroke-width:3px;stroke-linejoin:round;}
.pitch-legend {display:flex;flex-wrap:wrap;gap:6px 14px;justify-content:center;
  margin:10px 0 0;font-size:11px;color:var(--muted);}
.pitch-legend i {display:inline-block;width:8px;height:8px;border-radius:50%;
  margin-right:4px;vertical-align:-1px;}
.bench-strip {display:flex;flex-wrap:wrap;gap:6px;margin-top:12px;padding-top:12px;
  border-top:1px solid var(--line);align-items:center;}
.bench-label {font-size:11px;color:var(--faint);text-transform:uppercase;
  letter-spacing:.06em;margin-right:4px;}
.bench-pill {display:inline-flex;align-items:center;gap:6px;padding:4px 9px;
  border-radius:6px;background:var(--subtle);border:1px solid var(--line);
  color:var(--ink);font-size:12px;}
.bench-pill:hover {border-color:var(--muted);}
@media (max-width:850px) {
  .guide-grid {grid-template-columns:repeat(2,minmax(0,1fr));}
}
@media (max-width:560px) {
  .guide-grid, .ai-pillars {grid-template-columns:1fr;}
}
"""

JS = (
    r"""(() => {
  "use strict";
  const D = JSON.parse(document.getElementById("workspace-data").textContent),
    S = D.strategy || {},
    P = S.portfolio || {},
    summary = S.summary || {};
  const esc = (v) =>
    String(v ?? "").replace(
      /[&<>"']/g,
      (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c],
    );
  const arr = (v) => (Array.isArray(v) ? v : []),
    num = (v) => (typeof v === "number" && Number.isFinite(v) ? v : null);
  const fmt = (v, d = 0) =>
    num(v) === null
      ? "—"
      : new Intl.NumberFormat("de-DE", {
          maximumFractionDigits: d,
          minimumFractionDigits: d,
        }).format(v);
  const money = (v) =>
"""
    '    num(v) === null ? "—" : Math.abs(v) >= 1e6 ? fmt(v / 1e6, 2) + " Mio. €" : fmt(v) + " '
    '€";\n'
    r"""  const pct = (v) => (num(v) === null ? "—" : (v > 0 ? "+" : "") + fmt(v, 1) + " %"),
    cls = (v) => (num(v) === null ? "" : v < 0 ? "negative" : v > 0 ? "positive" : "");
  const safeURL = (v) => {
    try {
      const u = new URL(String(v));
      return ["http:", "https:"].includes(u.protocol) ? u.href : "";
    } catch {
      return "";
    }
  };
  const date = (v) => {
    if (!v) return "Datum offen";
    const t = new Date(v);
    return Number.isNaN(+t)
      ? String(v)
"""
    '      : t.toLocaleDateString("de-DE", { day: "2-digit", month: "2-digit", year: "numeric" '
    '});\n'
    r"""  };
  const squad = arr(D.kader),
    market = arr(D.market).filter((p) => !p.ours),
    liveRank = arr(D.ranking),
    bids = arr(D.bids);
  const articleExpired = (n) =>
    Number.isFinite(Date.parse(n.expires_at)) && Date.parse(n.expires_at) <= Date.now();
  const currentLineupArticle = (n) =>
    n.event_type === "confirmed_lineup" &&
    ["bench", "starter"].includes(n.role_status) &&
    num(n.matchday) !== null &&
    Number.isFinite(Date.parse(n.published_at)) &&
    Date.parse(n.published_at) <= Date.now() &&
    Number.isFinite(Date.parse(n.expires_at)) &&
    !articleExpired(n) &&
    !!safeURL(n.url);
  const ownIds = new Set(squad.map((p) => String(p.pid))),
    seenNews = new Set();
  const news = (arr(S.news?.articles).length ? arr(S.news.articles) : arr(D.news))
    .slice()
    .sort((a, b) => {
      const group = (n) =>
        articleExpired(n)
          ? 2
          : currentLineupArticle(n) && ownIds.has(String(n.player_id))
            ? 0
            : 1;
      return (
        group(a) - group(b) ||
        (Date.parse(b.published_at) || 0) - (Date.parse(a.published_at) || 0)
      );
    })
    .filter((n) => {
      const key = String(n.player_id || "") + "|" + String(n.url || n.title || "");
      if (seenNews.has(key)) return false;
      seenNews.add(key);
      return true;
    });
  const completedRows = arr(S.manager_profiles).length
      ? arr(S.manager_profiles)
      : arr(S.manager_stats),
    completedAvailable =
      liveRank.length > 0 &&
      liveRank.every((r) =>
        completedRows.some(
"""
    '          (m) => String(m.user_id) === String(r.user_id) && num(m.completed_points) !== nu'
    'll,\n'
    r"""        ),
      ),
    rank = completedAvailable
      ? liveRank
          .map((r) => ({
            ...r,
            points: completedRows.find((m) => String(m.user_id) === String(r.user_id))
              .completed_points,
          }))
          .sort((a, b) => b.points - a.points)
          .map((r, i, all) => ({
            ...r,
            rank: all.findIndex((x) => x.points === r.points) + 1,
          }))
      : liveRank,
    scoreBasis = completedAvailable
      ? "Abgeschlossene Spieltage"
      : "Live-Tabelle · Zwischenstand",
    deadline = S.deadline || {};
  const my = rank.find((r) => r.me) || D.mine || {},
    meName = rank.find((r) => r.me)?.name || "emil",
    ownManager = arr(D.manager_squads).find((m) => m.me);
  const managerProfiles = arr(S.manager_profiles).length
    ? arr(S.manager_profiles)
    : arr(D.manager_squads).map((m) => ({
        user_id: String(m.user_id),
        name: m.name,
        me: !!m.me,
"""
    '        live_points: liveRank.find((r) => String(r.user_id) === String(m.user_id))?.points'
    ',\n'
    '        completed_points: completedRows.find((r) => String(r.user_id) === String(m.user_id'
    '))\n'
    r"""          ?.completed_points,
        summary: { squad_value_eur: m.teamwert, budget_eur: m.me ? D.budget : null },
        holdings: [],
        trades: [],
        round_trips: [],
        matchday_history: [],
        financial_timeline: [],
      }));
  let overviewManager = String(
      managerProfiles.find((m) => m.me)?.user_id ||
        my.user_id ||
        managerProfiles[0]?.user_id ||
        "",
    ),
    pointsMode = "total",
    financeMode = "profit",
    tradeFilter = "all",
    activeManagerProfile = null;
  try {
    const saved = localStorage.getItem("kickbase.overview.manager");
    if (managerProfiles.some((m) => String(m.user_id) === saved)) overviewManager = saved;
  } catch {}
  const lineupCount = num(P.occupied_slots) ?? num(ownManager?.in_elf),
    missing = lineupCount === null ? null : Math.max(0, 11 - lineupCount);
  const cash = num(P.budget_eur) ?? num(D.budget);
  const bidTotal =
    num(P.open_bid_exposure_eur) ??
    (Array.isArray(D.bids) && bids.every((b) => num(b.amount) !== null)
      ? bids.reduce((t, b) => t + b.amount, 0)
      : null);
  const unbound = cash !== null && bidTotal !== null ? cash - bidTotal : null,
    teamValue = num(D.teamwert);
"""
    '  const largest = [...squad].filter((p) => num(p.mv) !== null).sort((a, b) => b.mv - a.mv)'
    '[0],\n'
    r"""    largestShare = largest && teamValue > 0 ? (largest.mv / teamValue) * 100 : null;
  const targetRank = Math.max(1, Math.ceil(rank.length / 3)),
    target = [...rank].sort((a, b) => b.points - a.points)[targetRank - 1],
    gap =
      num(target?.points) !== null && num(my.points) !== null
        ? Math.max(0, target.points - my.points)
        : null;
  let view = "heute",
    query = "",
    position = "",
    selectedManager = "",
    period = "all",
    includeFloor = false,
    showOwn = true,
    showOthers = true,
    historyPeriod = "all",
    activePlayer = null,
    effPool = "all",
    effSort = "avg_per_m",
    effQuery = "",
    effPos = "",
    effShowAll = false;
  let packageItems = [],
    reserve = "",
    lastFocus = null;
  const posCodeMap = { TW: 1, ABW: 2, MIT: 3, STU: 4, ST: 4 },
    posNameMap = { 1: "TW", 2: "ABW", 3: "MIT", 4: "STU" },
    normSearch = (v) =>
      String(v || "")
        .normalize("NFD")
        .replace(/[\u0300-\u036f]/g, "")
        .replace(/ß/g, "ss")
        .toLowerCase(),
    cloudPlayers = arr(D.fair?.cloud).map((c) => ({
      ...c,
      pid: String(c.pid || c.player_id || ""),
      avg: num(c.avg ?? c.ap),
      ap: num(c.ap ?? c.avg),
      team: c.team || "",
      pos: c.pos || "",
      position: num(c.position) ?? (posCodeMap[c.pos] || null),
      status: "unknown",
    })),
    otherPlayers = arr(D.manager_squads).flatMap((m) =>
      arr(m.spieler).map((p) => ({
        ...p,
        avg: p.avg_points,
        team: p.team || "",
        status: "unknown",
        fit: num(p.fit) === null ? null : p.fit === 0,
        owner_name: m.name || "",
        owner_id: String(m.user_id || ""),
        owner_me: !!m.me,
        in_elf: !!p.in_elf,
      })),
    ),
    historicalPlayers = arr(S.players).map((p) => {
      const stats = arr(p.season_stats),
        curStat = stats.find((s) => s.season === "2026/2027") || stats[0] || null,
        seasonKey = curStat?.season || "2026/2027",
        perf = arr(p.performance).filter(
          (r) => r.season === seasonKey && num(r.points) !== null,
        ),
        totPts = perf.length ? perf.reduce((acc, r) => acc + r.points, 0) : null,
        avgPts = num(curStat?.avg_points),
        posNum = num(p.position);
      return {
        pid: String(p.player_id),
        name: p.name,
        mv: p.market_value,
        position: posNum,
        pos: posNameMap[posNum] || "",
        avg: avgPts !== null ? Math.round(avgPts) : null,
        ap: avgPts !== null ? Math.round(avgPts) : null,
        punkte: totPts,
        einsaetze: num(curStat?.n_appearances),
        status: p.status || "unknown",
        kb_prob: num(p.kb_prob),
      };
    }),
    players = new Map();
  for (const src of [historicalPlayers, cloudPlayers, otherPlayers, market, squad]) {
    for (const p of src) {
      const id = String(p.pid || "");
      if (!id) continue;
      const prev = players.get(id) || {},
        merged = { ...prev };
      for (const [k, v] of Object.entries(p)) {
        if (v !== undefined && v !== null && v !== "") merged[k] = v;
        else if (!(k in merged)) merged[k] = v;
      }
      const names = [prev.search_name, prev.name, p.name].filter(Boolean);
      merged.search_name = [...new Set(names)].join(" ");
      players.set(id, merged);
    }
  }
  const playerData = new Map(arr(S.players).map((p) => [String(p.player_id), p]));
  const rows = arr(S.trades),
    priceBuckets = arr(S.price_buckets),
    managerStats = arr(S.manager_stats);
  const chartDate =
    Date.parse(S.generated_at) ||
    rows.reduce((v, r) => Math.max(v, Date.parse(r.at || "") || 0), 0) ||
    Date.now();
  const chip = (text, tone = "") => `<span class="tag ${tone}">${esc(text)}</span>`;
  const portrait = (p) => {
    const u = safeURL(p.img);
    return `<span class="portrait">${
      u
        ? `<img src="${esc(u)}" alt="" loading="lazy" onerror="this.remove()">`
        : esc(
            String(p.name || "?")
              .split(" ")
              .map((x) => x[0])
              .slice(0, 2)
              .join(""),
          )
    }</span>`;
  };
  const playerName = (p) =>
"""
    '    `<button class="player-name" data-player="${esc(p.pid)}">${portrait(p)}<span><strong>$'
    '{esc(p.name || "Spieler")}</strong><small>${esc(p.team || p.pos || "Verein unbekannt")}${p'
    '.team && p.pos ? " · " + esc(p.pos) : ""}</small></span></button>`;\n'
    r"""  const title = (h, p) =>
"""
    '    `<div class="page-title"><div><span class="eyebrow">SAISON 2026 / 27</span><h1>${h}</h'
    '1><p>${p}</p></div>${chip("Nächste Kaderfrist · " + (deadline.next_lock_label || "noch nic'
    'ht bestätigt"))}</div>`;\n'
    r"""  const metric = (label, value, context, tone = "") =>
"""
    '    `<div class="metric"><span>${esc(label)}</span><strong class="${tone}">${value}</stron'
    'g><small>${context}</small></div>`;\n'
    r"""  const note = (text, more = "") =>
"""
    '    `<div class="evidence">${text}${more ? `<details><summary>So wird gerechnet</summary><'
    'p>${more}</p></details>` : ""}</div>`;\n'
    r"""  const empty = (text) => `<div class="empty">${text}</div>`;
  const panel = (heading, sub, body, extra = "") =>
"""
    '    `<article class="panel ${extra}"><div class="panel-head"><div><h2>${heading}</h2>${sub'
    ' ? `<p>${sub}</p>` : ""}</div></div>${body}</article>`;\n'
    r"""  const unit = (text) => `<span class="unit">${esc(text)}</span>`;
  function relatedNews(p) {
    const parts = String(p.name || "")
      .toLowerCase()
      .split(/\s+/)
      .filter((x) => x.length > 3);
    return news.filter((n) =>
      n.player_id
        ? String(n.player_id) === String(p.pid)
        : parts.some((x) =>
            String(n.title || "")
              .toLowerCase()
              .includes(x),
          ),
    );
  }
  function confirmedLineup(p) {
"""
    '    return news.find((n) => String(n.player_id) === String(p.pid) && currentLineupArticle('
    'n));\n'
    r"""  }
  function observedRoleNote(p) {
    const n = confirmedLineup(p);
    return n
      ? note(
"""
    '          `Bestätigte Aufstellung für Spieltag ${esc(n.matchday)}. Keine Aussage über künf'
    'tige Startelfplätze. <a href="${esc(safeURL(n.url))}" target="_blank" rel="noopener norefe'
    'rrer">Originalquelle: ${esc(n.source || "Aufstellung")} ↗</a>`,\n'
    r"""        )
      : "";
  }
  function status(p) {
    const n = confirmedLineup(p),
      observed = n
        ? chip(
"""
    '            `Bestätigt ST${n.matchday}: ${n.role_status === "bench" ? "Bankstart" : "Start'
    'elf"}`,\n'
    r"""            n.role_status === "bench" ? "warn" : "good",
          ) + " "
        : "";
    return observed + predictedStatus(p);
  }
  function newsHTML(items = news.slice(0, 4)) {
    return items.length
      ? items
          .map((n) => {
            const u = safeURL(n.url),
              expired = articleExpired(n);
"""
    '            return `<div class="news-item">${n.player_name ? `<span class="eyebrow">${esc('
    'n.player_name)}</span>` : ""}${u ? `<a href="${esc(u)}" target="_blank" rel="noopener nore'
    'ferrer">${esc(n.title)} ↗</a>` : `<strong>${esc(n.title)}</strong>`}${n.implication ? `<p '
    'class="explanation">${esc(n.implication)}</p>` : ""}<div class="news-meta"><span>${esc(n.s'
    'ource || "Quelle nicht benannt")}</span><span>${esc(n.ts || date(n.published_at))}</span><'
    '/div>${n.uncertainty ? note(esc(n.uncertainty)) : ""}${expired ? chip("Erneut prüfen · Bel'
    'eg veraltet", "warn") : ""}</div>`;\n'
    r"""          })
          .join("")
      : empty(
"""
    '          "Noch keine passenden Nachrichten erfasst. Fehlende Meldungen sind kein Beleg fü'
    'r Fitness oder eine sichere Startelf.",\n'
    r"""        );
  }
  function budgetChart() {
    const data = [
        ["Konto", cash, ""],
        ["Offene Gebote", bidTotal, "muted"],
        ["Ohne neue Verkäufe", unbound, unbound !== null && unbound < 0 ? "danger" : ""],
      ],
      scale = Math.max(...data.map((x) => Math.abs(x[1] || 0)), 1);
"""
    '    return `<div class="budget-list">${data.map(([label, value, tone]) => `<div class="bud'
    'get-row"><span>${label}</span><div class="budget-track"><div class="budget-fill ${tone}" s'
    'tyle="width:${num(value) === null ? 0 : (Math.abs(value) / scale) * 100}%"></div></div><st'
    'rong class="${value !== null && value < 0 ? "negative" : ""}">${money(value)}</strong></di'
    'v>`).join("")}</div>${note("Momentaufnahme · Konto abzüglich aller erfassten offenen Gebot'
    'e.", "Keine Kreditlinie und keine erwarteten Verkaufserlöse als frei verfügbares Geld. Die'
    ' Reserve für fehlende Plätze wird im Paketvergleich zusätzlich eingetragen.")}`;\n'
    r"""  }
  function predictedStatus(p) {
    if (p.status === "out" || p.fit === false) return chip("Ausfall / prüfen", "bad");
    if (p.status === "bench") return chip("LI-Prognose: Bank", "warn");
    if (p.status === "starter") return chip("LI-Prognose: Startelf", "good");
    if ([1, 2].includes(p.kb_prob))
      return chip(p.kb_prob === 1 ? "KB: sicher" : "KB: erwartet");
    return chip("Rolle offen", "warn");
  }
  function squadFiltered() {
    return squad.filter(
      (p) =>
        (!query ||
          String(p.name || "")
            .toLowerCase()
            .includes(query.toLowerCase())) &&
        (!position || String(p.position) === position),
    );
  }
  function scatterSquad(items) {
    const valid = items.filter((p) => num(p.mv) !== null && num(p.avg ?? p.ap) !== null);
    if (!valid.length)
"""
    '      return empty("Für den Vergleich fehlen Marktwerte oder verifizierte Punkteschnitte."'
    ');\n'
    r"""    const W = 700,
      H = 295,
      L = 47,
      R = 20,
      T = 20,
      B = 43,
      mx = Math.max(...valid.map((p) => p.mv / 1e6), 1) * 1.12,
      lo = Math.min(0, ...valid.map((p) => p.avg ?? p.ap)),
      hi = Math.max(...valid.map((p) => p.avg ?? p.ap), 1) * 1.18,
      y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
    let c = "";
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
"""
    '      c += `<line class="grid-line" x1="${L}" y1="${y(v)}" x2="${W - R}" y2="${y(v)}"/><te'
    'xt x="${L - 10}" y="${y(v) + 4}" text-anchor="end">${fmt(v)}</text>`;\n'
    r"""    }
    for (let i = 0; i <= 4; i++) {
      const v = (mx * i) / 4;
"""
    '      c += `<text x="${L + (v / mx) * (W - L - R)}" y="${H - 20}" text-anchor="middle">${f'
    'mt(v)}</text>`;\n'
    r"""    }
    for (const p of valid) {
      const x = L + (p.mv / 1e6 / mx) * (W - L - R),
        yy = y(p.avg ?? p.ap);
"""
    '      c += `<circle cx="${x}" cy="${yy}" r="7" fill="${p.status === "out" ? "var(--bad)" :'
    ' "var(--chart)"}" opacity=".86" tabindex="0" role="button" aria-label="${esc(p.name)} öffn'
    'en" data-player="${esc(p.pid)}" data-tip="${esc(p.name + "\\nMarktwert: " + money(p.mv) + "'
    '\\nØ Punkte: " + fmt(p.avg ?? p.ap) + "\\nÖffnen für Details")}"></circle>`;\n'
    r"""      if (valid.length <= 12)
"""
    '        c += `<text x="${Math.min(x + 12, W - 75)}" y="${yy - 11}" style="fill:var(--muted'
    ')">${esc(p.name)}</text>`;\n'
    r"""    }
"""
    '    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Marktwe'
    'rt und tatsächlicher Punkteschnitt der Kaderspieler"><text class="axis-label" x="${L}" y="'
    '10">Ø Punkte pro Einsatz</text>${c}<text class="axis-label" x="${W - R}" y="${H}" text-anc'
    'hor="end">Marktwert in Mio. €</text></svg></div>${note(`${valid.length} von ${items.length'
    '} Spielern mit beiden Datenpunkten. Aktueller Marktwert · tatsächlich gemeldeter Punktesch'
    'nitt.`, "Kein Prognosemodell: Einsatzminuten, Gegner, Verletzungen und bisherige Einsätze '
    'müssen zusätzlich geprüft werden. Kaderpunkte sind keine Manager-Saisonpunkte.")}`;\n'
    r"""  }
  function squadTable(items) {
    return items.length
"""
    '      ? `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th>Rolle / Quelle</th>'
    '<th class="num">Marktwert</th><th class="num">24 Stunden</th><th class="num">Ø Punkte</th>'
    '<th class="num">Einsätze</th><th></th></tr></thead><tbody>${items.map((p) => `<tr><td>${pl'
    'ayerName(p)}</td><td>${status(p)}${p.protected ? " " + chip("Geschützt") : ""}</td><td cla'
    'ss="num">${money(p.mv)}</td><td class="num ${cls(p.mv24)}">${money(p.mv24)}</td><td class='
    '"num">${fmt(p.avg ?? p.ap)}</td><td class="num">${fmt(p.einsaetze)}</td><td><button class='
    '"text-link" data-player="${esc(p.pid)}" aria-label="${esc(p.name)} im Detail">↗</button></'
    'td></tr>`).join("")}</tbody></table></div>`\n'
    r"""      : empty("Keine Spieler für diese Auswahl.");
  }
  function calculator() {
    const held = new Set(bids.map((b) => String(b.player_id))),
      choices = market.filter(
        (p) =>
          !held.has(String(p.pid)) &&
          !packageItems.some((i) => String(i.pid) === String(p.pid)),
      );
"""
    '    return `<article class="panel" id="calculator"><div class="panel-head"><div><h2>Was pa'
    'sst gemeinsam ins Budget?</h2><p>Lokales Szenario: alle offenen Gebote plus dein ausgewähl'
    'tes Spielerpaket.</p></div>${chip("Szenario", "blue")}</div><div class="calc-grid"><div><d'
    'iv class="calc-control"><select id="package-player" aria-label="Marktspieler zum Paket hin'
    'zufügen"><option value="">Spieler vom Markt wählen</option>${choices\n'
    r"""      .filter((p) => num(p.mv) !== null)
      .map(
        (p) =>
"""
    '          `<option value="${esc(p.pid)}">${esc(p.name)} · ${money(p.mv)} · ${esc(p.pos || '
    '"")}</option>`,\n'
    r"""      )
      .join(
        "",
"""
    '      )}</select><button class="button secondary" id="package-add">Hinzufügen</button></di'
    'v><div class="calc-items">${packageItems.length ? packageItems.map((p, i) => `<div class="'
    'calc-item"><span>${esc(p.name)}<small style="display:block;color:var(--faint)">${esc(p.pos'
    ' || "")} · MW ${money(p.mv)}</small></span><input type="number" min="0" step="1" value="${'
    'num(p.amount) === null ? "" : p.amount}" data-package-amount="${i}" aria-label="Szenariopr'
    'eis für ${esc(p.name)} in Euro"><button data-package-remove="${i}" aria-label="${esc(p.nam'
    'e)} aus Paket entfernen">×</button></div>`).join("") : empty("Wähle Marktspieler und trage'
    ' deine eigenen Preisannahmen ein.")}</div><p class="calc-note">Voreinstellung ist der Mark'
    'twert, keine Gebotsempfehlung. Bereits offene Gebote werden nicht doppelt hinzugefügt. Es '
    'wird nichts gekauft oder verkauft.</p></div><div class="calc-summary"><label for="reserve"'
    '>Zusätzliche Reserve für restliche Elfplätze (€)</label><input id="reserve" type="number" '
    'min="0" step="1" placeholder="Bewusst eintragen, auch 0" value="${esc(reserve)}"><div clas'
    's="compact-label">Verbleibt nach allen Zuschlägen und Reserve</div><div class="calc-total"'
    ' id="calc-total">—</div><p class="calc-result" id="calc-result" aria-live="polite"></p></d'
    'iv></div></article>`;\n'
    r"""  }
  function updateCalc() {
    const total = document.getElementById("calc-total"),
      result = document.getElementById("calc-result");
    if (!total) return;
    const r = reserve === "" ? null : Number(reserve),
      validItems = packageItems.every(
        (p) => num(p.amount) !== null && p.amount >= 0 && Number.isInteger(p.amount),
      );
"""
    '    if (unbound === null || r === null || !Number.isSafeInteger(r) || r < 0 || !validItems'
    ') {\n'
    r"""      total.textContent = "—";
      total.className = "calc-total";
      result.textContent =
        unbound === null
          ? "Konto oder offene Gebote fehlen. Finanzierung noch nicht belegbar."
          : !validItems
"""
    '            ? "Jeder Spielerpreis muss eine vollständige, nicht negative Euro-Angabe sein.'
    '"\n'
    '            : "Restreserve bewusst als ganze Euro-Angabe festlegen. Ohne sie ist das Paket'
    ' nicht vollständig bewertet.";\n'
    r"""      return;
    }
    const cost = packageItems.reduce((t, p) => t + p.amount, 0),
      left = unbound - cost - r;
    total.textContent = money(left);
    total.className = "calc-total " + (left < 0 ? "negative" : "");
    result.textContent =
      left < 0
"""
    '        ? "Das Paket ist ohne zusätzliche, bereits realisierte Erlöse nicht vollständig fi'
    'nanziert."\n'
    '        : `Rechnerisch gedeckt. Startelfrollen, Positions- und Vereinsgrenzen sowie die Ab'
    'wicklung vor Anpfiff sind zusätzlich zu prüfen. Das Paket umfasst ${packageItems.length} S'
    'pieler für ${missing === null ? "unbekannt viele" : missing} offene Plätze; die Formations'
    'passung ist noch nicht bewertet.`;\n'
    r"""  }
  function validTrade(r) {
    return (
      num(r.premium_pct) !== null &&
      num(r.market_value) !== null &&
      r.market_value > 0 &&
      !!r.buyer &&
      !r.update_boundary
    );
  }
  function filterTrades() {
    return rows.filter((r) => {
      const t = Date.parse(r.at || "") || 0;
      return (
        validTrade(r) &&
        (!selectedManager || r.manager === selectedManager) &&
        (period === "all" || chartDate - t <= Number(period) * 86400000) &&
        (includeFloor || r.market_value > 500000) &&
        ((r.manager === meName && showOwn) || (r.manager !== meName && showOthers))
      );
    });
  }
  function premiumChart() {
    const data = filterTrades(),
"""
    '      legend = `<div class="chart-legend"><button class="chart-key-button" data-series="ow'
    'n" aria-pressed="${showOwn}"><i></i>${esc(meName)}</button><button class="chart-key-button'
    '" data-series="others" aria-pressed="${showOthers}"><i style="background:#aaa"></i>Andere '
    'Manager</button><span>${data.length} beobachtete Käufe</span></div>`;\n'
    r"""    if (!data.length)
      return (
        empty(
          rows.length
            ? "Keine belegten Kaufaufschläge für diese Auswahl."
            : "Kaufpreise und damalige Marktwerte werden noch zusammengeführt.",
        ) + legend
      );
    const W = 700,
      H = 305,
      L = 52,
      R = 20,
      T = 20,
      B = 42,
      mx = Math.max(...data.map((r) => r.market_value / 1e6), 1) * 1.08,
      lo = Math.min(-5, ...data.map((r) => r.premium_pct)) * 1.08,
      hi = Math.max(10, ...data.map((r) => r.premium_pct)) * 1.12,
      y = (v) => H - B - ((v - lo) / (hi - lo)) * (H - T - B);
    let c = "";
    for (let i = 0; i <= 4; i++) {
      const v = lo + ((hi - lo) * i) / 4;
"""
    '      c += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}"/><te'
    'xt x="${L - 9}" y="${y(v) + 4}" text-anchor="end">${fmt(v)} %</text>`;\n'
    r"""    }
    c += `<line class="zero-line" x1="${L}" x2="${W - R}" y1="${y(0)}" y2="${y(0)}"/>`;
    for (let i = 0; i <= 4; i++) {
      const v = (mx * i) / 4;
"""
    '      c += `<text x="${L + (v / mx) * (W - L - R)}" y="${H - 18}" text-anchor="middle">${f'
    'mt(v)}</text>`;\n'
    r"""    }
    for (const r of data) {
      const x = L + (r.market_value / 1e6 / mx) * (W - L - R),
        own = r.manager === meName;
"""
    '      c += `<circle cx="${x}" cy="${y(r.premium_pct)}" r="${own ? 5 : 3.6}" fill="${own ? '
    '"var(--chart)" : "#aaa"}" opacity="${own ? 0.95 : 0.5}" tabindex="0" role="button" data-pl'
    'ayer="${esc(r.player_id)}" data-player-manager="${esc(r.manager)}" aria-label="${esc(r.nam'
    'e)} öffnen" data-tip="${esc(r.name + " · " + r.manager + "\\n" + date(r.at) + " · Kauf " + '
    'money(r.amount) + "\\nDamals MW " + money(r.market_value) + "\\nAufschlag " + pct(r.premium_'
    'pct))}"/>`;\n'
    r"""    }
"""
    '    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Beobach'
    'tete Kaufaufschläge nach damaligem Marktwert">${c}<text class="axis-label" x="${W - R}" y='
    '"${H}" text-anchor="end">Marktwert beim Kauf in Mio. €</text></svg></div>${legend}${note("'
    'Aufschlag = (Kaufpreis ÷ damaliger Marktwert − 1) × 100. " + (includeFloor ? "500.000-€-Fä'
    'lle enthalten." : "500.000-€-Fälle separat schaltbar."), "Gewinnerpreise sind keine Gebots'
    'verteilung. Verkäufe, das Marktwert-Updatefenster und Käufe ohne belastbare historische Ba'
    'sis bleiben außerhalb dieser Grafik.")}<details class="evidence"><summary>Die ${data.lengt'
    'h} Käufe als Tabelle</summary><div class="table-wrap"><table><thead><tr><th>Spieler / Mana'
    'ger</th><th>Datum</th><th class="num">Kaufpreis</th><th class="num">Aufschlag</th></tr></t'
    'head><tbody>${data.map((r) => `<tr><td>${esc(r.name)} · ${esc(r.manager)}</td><td>${esc(da'
    'te(r.at))}</td><td class="num">${money(r.amount)}</td><td class="num">${pct(r.premium_pct)'
    '}</td></tr>`).join("")}</tbody></table></div></details>`;\n'
    r"""  }
  function rankingChart() {
    const rs = [...rank].sort((a, b) => (a.rank || 99) - (b.rank || 99)),
      max = Math.max(...rs.map((r) => r.points || 0), 1);
    return rs.length
"""
    '      ? `<div class="rank-list">${rs.map((r) => `<div class="rank-row ${r.me ? "me" : ""} '
    '${r.rank === targetRank ? "target" : ""}"><span class="rank-index">${fmt(r.rank).padStart('
    '2, "0")}</span><span class="rank-label">${esc(r.name)}</span><span class="rank-bar"><i sty'
    'le="width:${Math.max(0, ((r.points || 0) / max) * 100)}%"></i></span><strong>${fmt(r.point'
    's)}</strong></div>`).join("")}</div>${note(scoreBasis + ". Die gestrichelte Linie markiert'
    ' das obere Drittel. " + (completedAvailable ? "Der laufende Spieltag wird separat betracht'
    'et." : "Livepunkte können sich noch verändern."))}`\n'
    r"""      : empty("Die aktuelle Ligatabelle konnte noch nicht geladen werden.");
  }
  function bucketTable() {
    return priceBuckets.length
"""
    '      ? `<div class="table-wrap"><table><thead><tr><th>Marktwert beim Kauf</th><th class="'
    'num">Preisbelege</th><th class="num">Medianaufschlag</th></tr></thead><tbody>${priceBucket'
    's.map((b) => `<tr><td>${esc(b.label)}</td><td class="num">${fmt(b.n_priced ?? b.n)}</td><t'
    'd class="num">${pct(b.median_premium_pct)}</td></tr>`).join("")}</tbody></table></div>${no'
    'te("Gesamter erfasster Zeitraum · unabhängig vom Diagrammfilter. Kleine Gruppen schwanken '
    'stark; die Tabelle beweist keine Ursache.")}`\n'
    '      : empty("Preisgruppen werden mit belastbaren historischen Vergleichskäufen sichtbar.'
    '");\n'
    r"""  }
  function pnlContent() {
"""
    '    return `<div class="two-stats"><div><div class="compact-stat ${cls(summary.realized_pn'
    'l_eur)}">${money(summary.realized_pnl_eur)}</div><div class="compact-label">Abgeschlossene'
    ' Käufe / Verkäufe</div></div><div><div class="compact-stat ${cls(summary.unrealized_pnl_eu'
    'r)}">${money(summary.unrealized_pnl_eur)}</div><div class="compact-label">Gehaltene Käufe '
    'gegen aktuellen MW</div></div></div>${\n'
    r"""      arr(S.round_trips).length
"""
    '        ? `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th class="num">Kauf<'
    '/th><th class="num">Verkauf</th><th class="num">Ergebnis</th></tr></thead><tbody>${arr(\n'
    r"""            S.round_trips,
          )
            .map(
              (r) =>
"""
    '                `<tr><td>${esc(r.name)}</td><td class="num">${money(r.buy_amount)}</td><td'
    ' class="num">${money(r.sell_amount)}</td><td class="num ${cls(r.pnl_eur)}">${money(r.pnl_e'
    'ur)}</td></tr>`,\n'
    r"""            )
            .join("")}</tbody></table></div>`
        : ""
"""
    '    }${note("Nur belegte Transfers mit identifizierbaren Anschaffungskosten. Initial zuget'
    'eilte Spieler und reine Gebots-Auditzeilen sind keine sicheren Kaufbelege.", "Buchergebnis'
    'se sind noch nicht realisiert. Punktbeiträge werden hier nicht in einen erfundenen Euro-We'
    'rt umgerechnet.")}`;\n'
    r"""  }
  function managerTable() {
    return managerStats.length
"""
    '      ? `<div class="table-wrap"><table><thead><tr><th>Manager</th><th class="num">Käufe</'
    'th><th class="num">Preisbelege</th><th class="num">Medianaufschlag</th><th class="num">Top'
    ' 3 im Kader</th></tr></thead><tbody>${managerStats.map((r) => `<tr><td><button class="text'
    '-link" data-manager="${esc(r.user_id || r.name)}">${esc(r.name)} ↗</button></td><td class='
    '"num">${fmt(r.purchases)}</td><td class="num">${fmt(r.n_priced)}</td><td class="num">${pct'
    '(r.median_premium_pct)}</td><td class="num">${num(r.top3_share_pct) === null ? "—" : fmt(r'
    '.top3_share_pct, 1) + " %"}</td></tr>`).join("")}</tbody></table></div>${note("Aufschläge '
    'aus vergleichbaren Käufen im Gesamtfenster. Die Kapitalbindung der drei größten Spieler st'
    'ammt aus dem aktuellen Kader; das erklärt nicht allein den Tabellenplatz.")}`\n'
    r"""      : empty("Noch keine belastbare Managerauswertung vorhanden.");
  }
  function planningNotes() {
    const notes = arr(S.planning_notes);
    return notes.length
"""
    '      ? `<details class="evidence"><summary>Warum der Planner Optionen verworfen hat (${no'
    'tes.length})</summary>${notes.map((n) => `<p style="margin-top:12px">${esc(typeof n === "s'
    'tring" ? n : n.reason || n.note || JSON.stringify(n))}</p>`).join("")}</details>`\n'
    r"""      : "";
  }
  function sourcesHTML() {
"""
    '    return `<div class="source-row"><strong>Kickbase · Kader, Markt und Ligatabelle</stron'
    'g>${chip("Beobachtet")}<p>Aktueller Abruf. Tabellenpunkte, Spielerwerte und Kontostand wer'
    'den getrennt verwendet.</p></div><div class="source-row"><strong>Ligatransfers und histori'
    'sche Marktwerte</strong>${chip(rows.length ? "Verknüpft" : "Noch offen")}<p>Nur zeitlich p'
    'assende Marktwerte begründen einen Kaufaufschlag. Updates, fehlende Preise und Minimumwert'
    'e werden gesondert behandelt.</p></div><div class="source-row"><strong>Startelf und Nachri'
    'chten</strong>${chip(news.length + " Meldungen")}<p>Spieler, Zeitpunkt, Originalquelle und'
    ' Auswirkung gehören zusammen. Fehlende News bedeuten nicht „einsatzbereit“.</p></div>${arr'
    '(\n'
    r"""      S.limitations,
    )
      .map((t) => `<div class="source-row"><p>${esc(t)}</p></div>`)
      .join(
        "",
"""
    '      )}${note("Nicht bekannt sind unterlegene Rivalengebote, deren verfügbare Kontostände'
    ' und sichere zukünftige Startelfplätze.")}${planningNotes()}`;\n'
    r"""  }
  function league() {
    const names = [
      ...new Set(
        rows
          .filter(validTrade)
          .map((r) => r.manager)
          .filter(Boolean),
      ),
    ].sort();
    return (
"""
    '      title("Die Liga. Auf einen Blick.", "Ein Manager öffnet seine vollständige Übersicht'
    '.") +\n'
    '      `<article class="panel"><div class="panel-head"><div><h2>Alle Manager</h2><p>${esc(s'
    'coreBasis)} · eigene Zeile hervorgehoben</p></div><button class="button secondary" data-ow'
    'n-overview>Mein Team →</button></div>${leagueManagers()}</article>\n'
    '      <div class="overview-charts"><article class="panel"><div class="panel-head"><h2>Beza'
    'hlte Kaufaufschläge</h2>${chip("Beobachtete Käufe")}</div><div class="toolbar"><select id='
    '"manager-filter" aria-label="Käufe nach Manager filtern"><option value="">Alle Manager</op'
    'tion>${names.map((n) => `<option ${selectedManager === n ? "selected" : ""}>${esc(n)}</opt'
    'ion>`).join("")}</select><div class="segmented">${[\n'
    r"""        ["7", "7 Tage"],
        ["14", "14 Tage"],
        ["all", "Gesamt"],
      ]
        .map(
          ([k, l]) =>
"""
    '            `<button data-period="${k}" class="${period === k ? "active" : ""}">${l}</butt'
    'on>`,\n'
    r"""        )
        .join(
          "",
"""
    '        )}</div></div><div id="premium-chart">${premiumChart()}</div><label class="floor-c'
    'ontrol"><input type="checkbox" id="floor-filter" ${includeFloor ? "checked" : ""}> Käufe a'
    'm Marktwertminimum einbeziehen</label></article>${panel("Gleiche Preisklasse. Fairer Vergl'
    'eich.", "Mediane und Stichprobenumfang statt einer pauschalen Preisregel.", bucketTable())'
    '}</div>${panel("Datenbasis", "Quellen und bekannte Grenzen.", sourcesHTML(), "quiet")}`\n'
    r"""    );
  }
  function leagueManagers() {
"""
    '    return `<div class="table-wrap"><table class="league-managers"><thead><tr><th>Rang</th'
    '><th>Manager</th><th class="num">Abgeschlossen</th><th class="num">Live</th><th class="num'
    '">Kaderwert</th><th class="num">Realisiert</th><th class="num">Nicht realisiert</th></tr><'
    '/thead><tbody>${[\n'
    r"""      ...managerProfiles,
    ]
      .sort(
        (a, b) =>
          (rank.find((r) => String(r.user_id) === String(a.user_id))?.rank || 99) -
          (rank.find((r) => String(r.user_id) === String(b.user_id))?.rank || 99),
      )
      .map((m) => {
        const r = rank.find((r) => String(r.user_id) === String(m.user_id)),
          f = m.summary || {};
"""
    '        return `<tr class="${m.me ? "my-manager" : ""}" data-manager="${esc(m.user_id)}" t'
    'abindex="0" role="button" aria-label="Übersicht von ${esc(m.name)} öffnen"><td>${fmt(r?.ra'
    'nk)}</td><td><strong>${esc(m.name)}</strong>${m.me ? " " + chip("Mein Team", "blue") : ""}'
    '</td><td class="num">${fmt(m.completed_points)}</td><td class="num">${fmt(m.live_points)}<'
    '/td><td class="num">${money(f.squad_value_eur)}</td><td class="num ${cls(f.realized_pnl_eu'
    'r)}">${money(f.realized_pnl_eur)}</td><td class="num ${cls(f.unrealized_pnl_eur)}">${money'
    '(f.unrealized_pnl_eur)}</td></tr>`;\n'
    r"""      })
      .join("")}</tbody></table></div>`;
  }
  function currentProfile() {
    return (
      managerProfiles.find((m) => String(m.user_id) === String(overviewManager)) ||
      managerProfiles.find((m) => m.me) ||
      managerProfiles[0] || { name: meName, summary: {}, holdings: [], trades: [] }
    );
  }
  function managerRoster(profile = currentProfile()) {
    const actual = arr(D.manager_squads).find(
        (m) => String(m.user_id) === String(profile.user_id),
      ),
      holdings = new Map(arr(profile.holdings).map((p) => [String(p.player_id), p])),
      base = actual
        ? arr(actual.spieler)
        : profile.me
          ? squad
          : arr(profile.holdings).map((p) => ({
              pid: p.player_id,
              name: p.name,
              mv: p.market_value,
            }));
    return base.map((p) => ({
      ...p,
      ...players.get(String(p.pid)),
      pid: String(p.pid),
      holding: holdings.get(String(p.pid)),
      manager_id: String(profile.user_id),
    }));
  }
  function managerSwitcher() {
"""
    '    return `<div class="manager-switcher"><div><label for="overview-manager">Manager</labe'
    'l><select id="overview-manager" aria-label="Manager für die Übersicht wählen">${managerPro'
    'files.map((m) => `<option value="${esc(m.user_id)}" ${String(m.user_id) === String(overvie'
    'wManager) ? "selected" : ""}>${esc(m.name)}${m.me ? " · Mein Team" : ""}</option>`).join("'
    '")}</select></div><button class="button secondary" data-own-manager>Mein Team</button></di'
    'v>`;\n'
    r"""  }
  function compactSeries(
    items,
    { field, xKey = "at", format = money, label = "", provisional = false } = {},
  ) {
    const located = items.filter((r) =>
"""
    '        xKey === "matchday" ? num(r[xKey]) !== null : Number.isFinite(Date.parse(r[xKey]))'
    ',\n'
    r"""      ),
      valid = located.filter((r) => num(r[field]) !== null);
    if (!valid.length) return empty("Für diesen Verlauf fehlen noch belegte Daten.");
    const xValue = (r) => (xKey === "matchday" ? r[xKey] : Date.parse(r[xKey])),
      ordered = [...located].sort((a, b) => xValue(a) - xValue(b)),
      W = 600,
      H = 210,
      L = 58,
      R = 20,
      T = 21,
      B = 34,
      low = Math.min(0, ...valid.map((r) => r[field])),
      high = Math.max(0, ...valid.map((r) => r[field])),
      range = high - low,
      first = xValue(ordered[0]),
      last = xValue(ordered.at(-1)),
      span = last - first || 1,
      x = (r) =>
        ordered.length === 1
          ? (W + L - R) / 2
          : L + ((xValue(r) - first) / span) * (W - L - R),
      y = (v) => (range ? H - B - ((v - low) / range) * (H - T - B) : (H - B + T) / 2);
    let content = "";
    const ticks = range ? [0, 1, 2, 3] : [0];
    for (const i of ticks) {
      const v = low + (range * i) / 3;
"""
    '      content += `<line class="grid-line" x1="${L}" x2="${W - R}" y1="${y(v)}" y2="${y(v)}'
    '"/><text x="${L - 9}" y="${y(v) + 4}" text-anchor="end">${esc(format(v))}</text>`;\n'
    r"""    }
    ordered.forEach((r, i) => {
      const prev = ordered[i - 1],
        known = num(r[field]) !== null,
        when = xKey === "matchday" ? "ST " + r.matchday : date(r[xKey]);
      if (known && prev && num(prev[field]) !== null)
"""
    '        content += `<line data-series-segment x1="${x(prev)}" y1="${y(prev[field])}" x2="$'
    '{x(r)}" y2="${y(r[field])}" stroke="var(--chart)" stroke-width="2" ${provisional && (r.pro'
    'visional || prev.provisional) ? \'stroke-dasharray="5 5"\' : ""}/>`;\n'
    r"""      if (known)
"""
    '        content += `<circle cx="${x(r)}" cy="${y(r[field])}" r="4" fill="${r.provisional ?'
    ' "var(--warn)" : "var(--chart)"}" tabindex="0" data-tip="${esc(when + "\\n" + format(r[fiel'
    'd]) + (r.provisional ? "\\nLaufender Spieltag · vorläufig" : ""))}"/>`;\n'
    r"""      if (xKey === "matchday" || i === 0 || i === ordered.length - 1)
"""
    '        content += `<text x="${x(r)}" y="${H - 9}" text-anchor="${xKey === "matchday" ? "m'
    'iddle" : i === 0 ? "start" : "end"}">${esc(when)}</text>`;\n'
    r"""    });
"""
    '    return `<div class="chart compact-chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-'
    'label="${esc(label)}">${content}</svg></div>${located.length > valid.length ? note(located'
    '.length - valid.length + " Werte unbekannt · Kurve dort unterbrochen.") : ""}`;\n'
    r"""  }
  function pointHistory(profile) {
    const history = arr(profile.matchday_history),
      seasons = [...new Set(history.map((r) => r.season))].sort(),
      latest = seasons.at(-1),
      current = history.filter((r) => r.season === latest);
    return (
      compactSeries(current, {
        field: pointsMode === "total" ? "cumulative_points" : "points",
        xKey: "matchday",
        format: (v) => fmt(v),
        label: "Saisonpunkte von " + profile.name,
        provisional: true,
      }) +
"""
    '      `<div class="chart-legend"><span>● Abgeschlossen</span>${current.some((r) => r.provi'
    'sional) ? \'<span class="warning">● Laufender Spieltag</span>\' : ""}<span>${latest ? esc(la'
    'test) + " · " : ""}${current.length} erfasste Spieltage</span></div>`\n'
    r"""    );
  }
  function financialHistory(profile) {
    return (
      compactSeries(arr(profile.financial_timeline), {
"""
    '        field: financeMode === "cashflow" ? "net_transfer_cashflow_eur" : "realized_pnl_eu'
    'r",\n'
    r"""        label: "Finanzverlauf von " + profile.name,
      }) +
      note(
        financeMode === "cashflow"
"""
    '          ? "Kumulierte Verkaufserlöse minus Kaufzahlungen. Kein Kontostand und kein Gewin'
    'n."\n'
    '          : "Kumuliertes Ergebnis belegter Kauf-Verkauf-Paare. Verkäufe ohne bekannten Ein'
    'stand bleiben getrennt.",\n'
    r"""      )
    );
  }
  const JEV = D.jev || {},
    jevByPlayer = JEV.spieler || {},
    jevFeed = arr(JEV.feed),
    intents = arr(D.intents),
    purchaseReviews = S.purchase_reviews || {},
    teamPlan = S.team_plan || {},
    forecasts = teamPlan.forecasts || {};
  let liveIntents = intents.map((i) => ({ ...i })),
    liveBids = Array.isArray(D.bids) ? bids.map((b) => ({ ...b })) : [],
    liveProtocol = arr(D.protocol).map((e) => ({ ...e })),
    openAmountIntentId = null,
    lastBackendMsg = "";
  function jevForPlayer(p) {
    if (!p) return [];
    const pid = String(p.pid || ""),
      pname = String(p.name || "").trim().toLowerCase(),
      raw = [
        ...arr(jevByPlayer[pid]),
        ...(pname ? arr(jevByPlayer["name:" + pname]) : []),
        ...jevFeed.filter(
          (j) =>
            j.status === "ok" &&
            ((pid && String(j.player_id || "") === pid) ||
              (pname &&
                String(j.player || "")
                  .toLowerCase()
                  .split(",")
                  .some((part) => part.trim() === pname))),
        ),
      ],
      seen = new Set(),
      out = [];
    for (const j of raw) {
      if (!j || j.status === "ausfall") continue;
      const k = `${j.kind}|${j.answer}|${j.outcome}|${j.subject || ""}`;
      if (seen.has(k)) continue;
      seen.add(k);
      out.push(j);
    }
    return out.sort((a, b) => (Date.parse(b.at || "") || 0) - (Date.parse(a.at || "") || 0));
  }
  function jevTranslate(j) {
    const pPct = num(j.p) !== null ? fmt(j.p * 100, 0) + " %" : "",
      pSuffix = pPct ? " · " + pPct : "",
      probLabels = {
        startet: "Startelf",
        bank: "Bank",
        fehlt: "Fehlt",
        kein_hinweis: "Kein Störfeuer",
        rueckkehr: "Rückkehr",
        ausfall: "Ausfall",
        kein_ereignis: "Neutral",
        tage: "Tage",
        wochen_1_3: "1–3 Wochen",
        wochen_3_8: "3–8 Wochen",
        monate: "Monate",
        bestimmt: "Bestimmt",
        diagnose: "Diagnose",
      },
      probs = Object.entries(j.probabilities || {})
        .filter(([, v]) => num(v) !== null && v >= 0.01)
        .sort((a, b) => b[1] - a[1])
        .map(([k, v], idx) => ({
          label: probLabels[k] || k,
          val: fmt(v * 100, 0) + " %",
          top: idx === 0,
        }));
    let kindLabel = "Jev News-Check",
      title = (j.answer || "Geprüft") + pSuffix,
      tone = "jev",
      explanation = "Nachrichten-Analyse durch das Sprachmodell Jev.";
    if (["rolle", "einsatz", "einsatz_urteil"].includes(j.kind)) {
      kindLabel = "Jev Einsatz-Check";
      if (j.answer === "fehlt") {
        const block =
          j.outcome === "sperrt" || j.outcome === "warnung" || (num(j.p) ?? 0) >= 0.95;
        tone = block ? "jev-bad" : "warn";
        title = (block ? "Fällt aus · Kauf-Sperre" : "Ausfall-Risiko") + pSuffix;
        explanation = block
          ? "Sicheres Ausfall-Signal (≥ 95 %) in den Meldungen — der Agent blockiert Käufe."
          : "Möglicher Ausfall erwähnt, liegt aber noch unter der harten 95-%-Sperrschwelle.";
      } else if (j.answer === "startet") {
        tone = "jev-good";
        title = "Startelf-Signal" + pSuffix;
        explanation = "Aktuelle Meldung spricht klar für einen Einsatz von Beginn an.";
      } else if (j.answer === "bank") {
        tone = "warn";
        title = "Bank- / Joker-Hinweis" + pSuffix;
        explanation = "Meldung deutet eher auf einen Bankplatz als auf die Startelf hin.";
      } else {
        tone = "jev-good";
        title = "News-Check sauber" + pSuffix;
        explanation = "Keine Ausfall- oder Bank-Warnung in der geprüften Meldung gefunden.";
      }
    } else if (j.kind === "rueckkehr") {
      kindLabel = "Jev Comeback-Radar";
      if (j.answer === "rueckkehr") {
        tone = "jev-good";
        title = "Comeback-Signal" + pSuffix;
        explanation =
          j.outcome === "kaufhinweis"
            ? "Kehrt nach Verletzung oder Sperre zurück — aktives Kaufsignal für den Agenten!"
            : "Rückkehr ins Training oder den Kader gemeldet.";
      } else if (j.answer === "ausfall") {
        tone = "jev-bad";
        title = "Weiterhin Ausfall" + pSuffix;
        explanation = "Meldung spricht gegen eine zeitnahe Rückkehr.";
      } else {
        tone = "jev";
        title = "Kein Comeback-Event" + pSuffix;
        explanation = "Normale Meldung ohne neues Rückkehr- oder Ausfall-Ereignis.";
      }
    } else if (j.kind === "schwere") {
      kindLabel = "Jev Verletzungs-Dauer";
      if (j.answer === "tage") {
        tone = "jev-good";
        title = "Kurzfristig · nur Tage" + pSuffix;
        explanation = "Nur kurze Pause erwartet — kein Grund für einen Notverkauf.";
      } else if (j.answer === "wochen_1_3") {
        tone = "warn";
        title = "Pause ca. 1–3 Wochen" + pSuffix;
        explanation = "Überbrückbare Pause — Verkauf nur bei starkem Stamm-Ersatz.";
      } else if (j.answer === "wochen_3_8") {
        tone = "jev-bad";
        title = "Pause ca. 3–8 Wochen" + pSuffix;
        explanation = "Fehlt mehrere Spieltage — Kapitalbindung kritisch prüfen.";
      } else if (j.answer === "monate") {
        tone = "jev-bad";
        title = "Langzeitausfall · Monate" + pSuffix;
        explanation =
          j.outcome === "sofortverkauf_moeglich"
            ? "Schwere Diagnose bestätigt — Sofortverkauf vom System freigegeben."
            : "Langwieriger Ausfall erwartet.";
      }
    }
    if (j.aufgehoben) {
      tone = "";
      title += " (später aufgehoben)";
    }
    return {
      kindLabel,
      title,
      tone,
      explanation,
      probs,
      subject: j.subject || "",
      source: j.source || "News",
      url: safeURL(j.url),
      at: j.published_at || j.at,
    };
  }
  function jevBadgeInline(p) {
    const items = jevForPlayer(p);
    if (!items.length) return "";
    const effects = ["sperrt", "warnung", "kaufhinweis", "sofortverkauf_moeglich"],
      neutral = ["kein_hinweis", "kein_ereignis"],
      prio =
        items.find((j) => !j.aufgehoben && effects.includes(j.outcome)) ||
        items.find((j) => !j.aufgehoben && j.answer && !neutral.includes(j.answer)) ||
        items[0],
      tr = jevTranslate(prio);
    return " " + chip("Jev: " + tr.title, tr.tone);
  }
  function agentVerdictForPlayer(p) {
    const pid = String(p?.pid || ""),
      rev = purchaseReviews[pid] || p?.purchase_review || null,
      liveSt = ["planned", "ready", "executing"],
      activeBid = liveIntents.find(
        (i) => String(i.player_id) === pid && i.kind === "bid" && liveSt.includes(i.status),
      ),
      activeSell = liveIntents.find(
        (i) =>
          String(i.player_id) === pid &&
          ["sell", "list"].includes(i.kind) &&
          liveSt.includes(i.status),
      ),
      swapOut = arr(teamPlan.swap_sales).find((s) => String(s.player_id) === pid),
      inPlanElf = arr(teamPlan.lineup_ids).map(String).includes(pid) || !!p?.elf;
    if (activeBid || rev?.selected) {
      const amt = num(activeBid?.amount) ?? num(rev?.target_bid_eur) ?? num(rev?.max_bid_eur),
        swapPid = rev?.swap_sale?.player_id,
        swapName = swapPid ? players.get(String(swapPid))?.name || "Tausch-Kandidat" : null,
        scen = arr(rev?.bid_scenarios).find((s) => s.selected) || arr(rev?.bid_scenarios)[0],
        gainPts =
          num(scen?.win?.points) !== null && num(scen?.loss?.points) !== null
            ? scen.win.points - scen.loss.points
            : null,
        proc = money(rev?.swap_sale?.proceeds_eur);
      return {
        tone: "good",
        badge: "Kauf geplant",
        headline: `Kauf-Vorhaben aktiv: Gebot ${money(amt)}`,
        detail: [
          swapName
            ? `Gekoppelter Tausch: ${swapName} wird erst nach Zuschlag für ${proc} verkauft.`
            : "Finanzierung aus verfügbarem Budget & Dispo-Rahmen gesichert.",
          gainPts !== null && gainPts > 0
            ? `Bringt deiner Startelf +${fmt(gainPts, 1)} erwartete Punkte über 4 Spieltage.`
            : "",
        ]
          .filter(Boolean)
          .join(" "),
      };
    }
    if (activeSell || swapOut) {
      const targetPid = swapOut?.for_player_id,
        targetName = targetPid ? players.get(String(targetPid))?.name || "Neuzugang" : null,
        amt = num(activeSell?.amount) ?? num(swapOut?.proceeds_eur);
      return {
        tone: "warn",
        badge: targetName ? "Tausch-Abgang" : "Verkauf geplant",
        headline: targetName
          ? `Geplanter Tausch-Verkauf (${money(amt)}) für ${targetName}`
          : `Verkaufs-Vorhaben aktiv (${money(amt)})`,
        detail: targetName
          ? `Sicherheits-Kopplung: Verkauf erst, sobald ${targetName} im Kader steht.`
          : activeSell?.rationale || "Schafft Budget bzw. räumt einen schwachen Kaderplatz.",
      };
    }
    if (ownIds.has(pid)) {
      if (p.protected) {
        return {
          tone: "good",
          badge: "Unverkäuflich",
          headline: "Geschützter Schlüsselspieler in deiner Startelf",
          detail: "Fester Bestandteil der Elf mit aktivem Verkaufsschutz.",
        };
      }
      return inPlanElf
        ? {
            tone: "good",
            badge: "In deiner Startelf",
            headline: "Vom Agenten für die aktuelle 11er-Startelf gesetzt",
            detail:
              num(p.mv24) !== null && p.mv24 >= 150000
                ? `Starker MW-Steiger (${money(p.mv24)}/Tag) — baut unter der Woche Wert auf.`
                : "Trägt aktiv zur Punkteprognose des kommenden Spieltags bei.",
          }
        : {
            tone: "warn",
            badge: "Kader / Bank",
            headline: "Aktuell Ergänzungs- oder Tausch-Kandidat im Kader",
            detail: "Bereit für Rotation oder als Baustein für ein Startelf-Upgrade.",
          };
    }
    if (rev) {
      const firstReason =
        arr(rev.reasons)[0] || "Verbessert die aktuelle Startelf-Prognose nicht.";
      return {
        tone: "",
        badge: "Geprüft · Kein Gebot",
        headline: "Vom Agenten im aktuellen Lauf übersprungen",
        detail: firstReason,
      };
    }
    return {
      tone: "",
      badge: "Beobachtet",
      headline: "Spieler im Liga- & Marktradar",
      detail: "Klicke durch Marktwert, Einsatzminuten und Jev-Checks für Details.",
    };
  }
  function aiFactor(p) {
    const pid = String(p?.pid || ""),
      fc = forecasts[pid] || null,
      rev = purchaseReviews[pid] || p?.purchase_review || null,
      jevItems = jevForPlayer(p).filter((j) => !j.aufgehoben),
      conf = confirmedLineup(p),
      expMin = num(fc?.expected_minutes) ?? num(p?.avg_min),
      hzPts = num(fc?.horizon_points),
      expPts = num(fc?.expected_points),
      avgPts = avgPointsOf(p),
      mv = num(p?.mv),
      avgR = ptsPerMio(avgPts, mv),
      fcR = ptsPerMio(hzPts, mv),
      fairDelta = num(p?.fair_delta_pct),
      mv24 = num(p?.mv24),
      inPlanElf = arr(teamPlan.lineup_ids).map(String).includes(pid) || !!p?.elf,
      fixtureLine =
        arr(fc?.reasons).find((r) => String(r).includes("Spielplan-Horizontfaktor")) || "",
      fixtureMatch = fixtureLine.match(/×([0-9.,]+)/),
      fixtureMult = fixtureMatch ? fixtureMatch[1] : null;
    let rolePts = 12,
      roleHint = "Rolle offen";
    if (p?.status === "out" || p?.fit === false) {
      rolePts = 2;
      roleHint = "Ausfall";
    } else if (conf?.role_status === "starter" || p?.status === "starter" || p?.kb_prob === 1) {
      rolePts = 22;
      roleHint = expMin !== null ? `Startelf · ${fmt(expMin, 0)} Min.` : "Startelf erwartet";
    } else if (p?.kb_prob === 2) {
      rolePts = 17;
      roleHint = expMin !== null ? `Erwartet · ${fmt(expMin, 0)} Min.` : "KB: erwartet";
    } else if (conf?.role_status === "bench" || p?.status === "bench") {
      rolePts = 8;
      roleHint = "Bank-Risiko";
    } else if (expMin !== null && expMin >= 65) {
      rolePts = 19;
      roleHint = `${fmt(expMin, 0)} Min. erwartet`;
    }
    if (fixtureMult) roleHint += ` · Spielplan ×${fixtureMult}`;
    if (p?.protected || inPlanElf) rolePts = Math.min(25, rolePts + 3);
    let ptsScore = 10,
      ptsHint = "Punkte offen";
    if (hzPts !== null) {
      ptsScore = Math.max(3, Math.min(25, Math.round((hzPts / 400) * 25)));
      ptsHint =
        `${fmt(hzPts, 0)} Pkt.` + (expPts !== null ? ` · Ø ${fmt(expPts, 1)} Pkt./Spiel` : "");
    } else if (avgPts !== null) {
      ptsScore = Math.max(3, Math.min(25, Math.round((avgPts / 135) * 25)));
      ptsHint = `Ø ${fmt(avgPts, 0)} Pkt.`;
    }
    let valScore = 11,
      valHint = "MW-Check";
    if (avgR !== null) {
      valScore = Math.max(3, Math.min(25, Math.round((avgR / 22) * 25)));
      valHint = `${fmtRatio(avgR)} Pkt./Mio.`;
    } else if (fcR !== null) {
      valScore = Math.max(3, Math.min(25, Math.round((fcR / 55) * 25)));
      valHint = `Prog. ${fmtRatio(fcR)}/Mio.`;
    }
    if (fairDelta !== null) {
      if (fairDelta > 8) valScore = Math.min(25, valScore + 3);
      valHint =
        valHint === "MW-Check"
          ? `Fair ${pct(fairDelta)}`
          : `${valHint} · Fair ${pct(fairDelta)}`;
    }
    let dynScore = 12,
      dynHint = "Stabil",
      blocked = p?.status === "out" || p?.fit === false;
    const topJev = jevItems[0];
    if (topJev) {
      if (topJev.outcome === "sperrt" || topJev.answer === "fehlt" || topJev.answer === "ausfall") {
        dynScore = 2;
        dynHint = "Jev: Warnung";
        if (topJev.outcome === "sperrt" || (num(topJev.p) ?? 0) >= 0.9) blocked = true;
      } else if (topJev.answer === "startet" || topJev.answer === "rueckkehr") {
        dynScore = 22;
        dynHint = topJev.answer === "rueckkehr" ? "Jev: Comeback" : "Jev: Startelf";
      } else if (topJev.answer === "bank") {
        dynScore = 7;
        dynHint = "Jev: Bank";
      } else {
        dynScore = 16;
        dynHint = "Jev: geprüft";
      }
    }
    if (mv24 !== null && mv24 >= 150000) {
      dynScore = Math.min(25, dynScore + 4);
      if (!topJev) dynHint = `MW +${fmt(mv24 / 1000, 0)}k/24h`;
    } else if (mv24 !== null && mv24 <= -150000) {
      dynScore = Math.max(2, dynScore - 3);
      if (!topJev) dynHint = `MW ${fmt(mv24 / 1000, 0)}k/24h`;
    }
    let score = Math.max(0, Math.min(100, Math.round(rolePts + ptsScore + valScore + dynScore)));
    if (blocked) score = Math.min(score, 24);
    if (rev?.selected) score = Math.max(score, 82);
    const tone = score >= 72 ? "good" : score >= 52 ? "blue" : score >= 36 ? "warn" : "bad",
      label =
        score >= 72
          ? "Starker KI-Faktor"
          : score >= 52
            ? "Solider Faktor"
            : score >= 36
              ? "Wackelkandidat"
              : "Risiko / Meiden";
    return {
      score,
      tone,
      label,
      pillars: [
        { label: "Startelf & Rolle", pts: rolePts, max: 25, hint: roleHint },
        { label: "4-ST-Punkte & Form", pts: ptsScore, max: 25, hint: ptsHint },
        { label: "Preis-Leistung", pts: valScore, max: 25, hint: valHint },
        { label: "Jev & Dynamik", pts: dynScore, max: 25, hint: dynHint },
      ],
    };
  }
  function aiFactorBadge(p) {
    const f = aiFactor(p);
    return (
      `<span class="ai-factor-pill ${f.tone}"` +
      ` title="AI-Entscheidungsfaktor: ${f.score}/100 (${esc(f.label)})">` +
      `AI ${f.score}<i>/100</i></span>`
    );
  }
  function aiFactorCell(p) {
    const f = aiFactor(p);
    return (
      `<div class="ai-factor-cell"><div class="ai-factor-top">${aiFactorBadge(p)}` +
      `<span class="ai-factor-bar ${f.tone}"><i style="width:${f.score}%"></i></span></div></div>`
    );
  }
  function aiFactorMatrixHTML(p) {
    const f = aiFactor(p);
    return (
      `<div class="ai-pillars" aria-label="4-Säulen-Aufschlüsselung des AI-Faktors">` +
      f.pillars
        .map((pl) => {
          const w = Math.max(6, Math.min(100, Math.round((pl.pts / pl.max) * 100)));
          return (
            `<div class="ai-pillar"><div class="ai-pillar-top">` +
            `<span>${esc(pl.label)}</span><strong>${pl.pts} / ${pl.max}</strong></div>` +
            `<div class="ai-pillar-track"><div class="ai-pillar-fill"` +
            ` style="width:${w}%"></div></div><small>${esc(pl.hint)}</small></div>`
          );
        })
        .join("") +
      `</div>`
    );
  }
  function intentActionsHTML(i) {
    if (!i || i.status !== "planned") return "";
    const id = esc(i.id),
      isOpen = String(openAmountIntentId) === String(i.id),
      curAmt = num(i.amount) ?? "";
    return (
      `<div class="intent-actions">` +
      `<button type="button" class="button btn-sm" data-intent-act="exec"` +
      ` data-intent-id="${id}" title="POST /api/intents/${id}/execute-now">` +
      `Sofort umsetzen</button>` +
      `<button type="button" class="button secondary btn-sm" data-intent-act="plus10"` +
      ` data-intent-id="${id}" title="POST /api/intents/${id}/adjust (+10 %)">` +
      `+10 %</button>` +
      `<button type="button" class="button secondary btn-sm" data-intent-act="plus20"` +
      ` data-intent-id="${id}" title="POST /api/intents/${id}/adjust (+20 %)">` +
      `+20 %</button>` +
      `<button type="button" class="button secondary btn-sm" data-intent-act="toggle-amount"` +
      ` data-intent-id="${id}">Betrag €</button>` +
      `<button type="button" class="button danger btn-sm" data-intent-act="veto"` +
      ` data-intent-id="${id}" title="POST /api/intents/${id}/veto">× Veto</button>` +
      `</div>` +
      `<div class="intent-inline-form" id="intent-amount-box-${id}" ${isOpen ? "" : "hidden"}>` +
      `<input type="number" min="1" step="1000" id="intent-amount-input-${id}"` +
      ` value="${curAmt}" placeholder="Neuer Betrag in €"` +
      ` aria-label="Neuer Betrag in Euro für Vorhaben ${id}">` +
      `<button type="button" class="button btn-sm" data-intent-act="amount"` +
      ` data-intent-id="${id}">Speichern</button>` +
      `<input type="text" id="intent-veto-note-${id}" placeholder="Optionale Veto-Notiz"` +
      ` aria-label="Optionale Veto-Notiz für Vorhaben ${id}">` +
      `</div>`
    );
  }
  function agentDossierHTML(p) {
    const pid = String(p?.pid || ""),
      verdict = agentVerdictForPlayer(p),
      jevItems = jevForPlayer(p).slice(0, 2),
      activeIntent = liveIntents.find(
        (i) => String(i.player_id) === pid && ["planned", "ready"].includes(i.status),
      ),
      jevBlock = jevItems.length
        ? `<div class="jev-box"><div class="jev-box-head">` +
          `<strong><span class="jev-pill">JEV</span> News-Prüfung</strong>` +
          `<button type="button" class="text-link" data-view="jev"` +
          ` style="font-size:11px;min-height:auto">Alle Jev-Urteile →</button></div>` +
          jevItems
            .map((j) => {
              const tr = jevTranslate(j);
              return (
                `<div class="jev-item"><div class="jev-item-top">` +
                `<strong>${esc(tr.kindLabel)}</strong>${chip(tr.title, tr.tone)}</div>` +
                `<p>${esc(tr.explanation)}</p>` +
                (tr.subject
                  ? `<div class="jev-source"><span>„${esc(tr.subject)}“ (${esc(
                      tr.source,
                    )})</span></div>`
                  : "") +
                `</div>`
              );
            })
            .join("") +
          `</div>`
        : "";
    return (
      `<div class="ai-dossier" aria-label="Einschätzung von Agent und Jev">` +
      `<div class="ai-dossier-head"><div>` +
      `<span class="eyebrow">KI-Entscheidungsfaktor (4 Säulen je 25 Pkt.)</span>` +
      `<h3>Einordnung zu ${esc(p.name)}</h3></div>` +
      `<div class="intent-title">${aiFactorBadge(p)} ${chip(verdict.badge, verdict.tone)}` +
      `</div></div>` +
      `<div class="ai-verdict ${verdict.tone}">` +
      `<strong>${esc(verdict.headline)}</strong>` +
      `<p>${esc(verdict.detail)}</p>` +
      (activeIntent ? intentActionsHTML(activeIntent) : "") +
      `</div>` +
      aiFactorMatrixHTML(p) +
      jevBlock +
      `</div>`
    );
  }
  function avgPointsOf(p) {
    return num(p?.avg ?? p?.ap ?? p?.avg_points);
  }
  function totalPointsOf(p) {
    return num(p?.punkte ?? p?.points);
  }
  function forecastPointsOf(p) {
    const fc = forecasts[String(p?.pid || "")];
    return num(fc?.horizon_points);
  }
  function ptsPerMio(pts, mv) {
    const pv = num(pts),
      mvVal = num(mv);
    return pv !== null && mvVal !== null && mvVal > 0 ? (pv / mvVal) * 1e6 : null;
  }
  function fmtRatio(v, d = 1) {
    return num(v) === null ? "—" : fmt(v, d);
  }
  function effTone(v, kind = "avg") {
    if (num(v) === null) return "";
    if (kind === "total") return v >= 80 ? "positive" : v < 25 ? "negative" : "";
    if (kind === "forecast") return v >= 45 ? "positive" : v < 15 ? "negative" : "";
    return v >= 15 ? "positive" : v < 5 ? "negative" : "";
  }
  function ppmCell(p) {
    const avgPts = avgPointsOf(p),
      totPts = totalPointsOf(p),
      fcPts = forecastPointsOf(p),
      avgR = ptsPerMio(avgPts, p?.mv),
      totR = ptsPerMio(totPts, p?.mv),
      fcR = ptsPerMio(fcPts, p?.mv),
      mainVal =
        avgR !== null
          ? fmtRatio(avgR) + " / Mio."
          : fcR !== null
            ? fmtRatio(fcR) + " / Mio."
            : "—",
      mainTone = avgR !== null ? effTone(avgR, "avg") : effTone(fcR, "forecast"),
      subParts = [];
    if (totR !== null) subParts.push("Ges. " + fmtRatio(totR) + "/Mio.");
    if (fcR !== null) subParts.push("Prog. " + fmtRatio(fcR) + "/Mio.");
    const subTxt = subParts.length
      ? `<small class="cell-sub">${esc(subParts.join(" · "))}</small>`
      : "";
    return `<td class="num ${mainTone}">${mainVal}${subTxt}</td>`;
  }
  function overviewSquadTable(items) {
    if (!items.length) return empty("Für diesen Manager wurde noch kein Kader erfasst.");
    return (
      `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th>Rolle &amp; Jev</th>` +
      `<th class="num">Marktwert</th><th class="num">Einstand</th>` +
      `<th class="num">Buchergebnis</th><th class="num">Ø Punkte</th>` +
      `<th class="num">Pkt. / Mio. €</th></tr></thead><tbody>` +
      items
        .map((p) => {
          const sub =
              `${esc(p.team || p.pos || "")}` + (p.team && p.pos ? " · " + esc(p.pos) : ""),
            mvSub =
              num(p.mv24) !== null && p.mv24 !== 0
                ? `<small class="cell-sub ${cls(p.mv24)}">${money(p.mv24)} / 24h</small>`
                : "",
            pnlSub =
              num(p.holding?.unrealized_pnl_pct) !== null
                ? `<small class="cell-sub">${pct(p.holding.unrealized_pnl_pct)}</small>`
                : "",
            totPts = totalPointsOf(p),
            ptsSub =
              totPts !== null
                ? `<small class="cell-sub">${fmt(totPts)} Saison-Pkt.</small>`
                : "";
          return (
            `<tr><td><button class="player-name" data-player="${esc(p.pid)}"` +
            ` data-player-manager="${esc(p.manager_id)}">${portrait(p)}<span>` +
            `<strong>${esc(p.name)}</strong><small>${sub}</small></span></button></td>` +
            `<td>${status(p)}${p.protected ? " " + chip("Geschützt") : ""}` +
            `${jevBadgeInline(p)}</td><td class="num">${money(p.mv)}${mvSub}</td>` +
            `<td class="num">${money(p.holding?.buy_amount)}</td>` +
            `<td class="num ${cls(p.holding?.unrealized_pnl_eur)}">` +
            `${money(p.holding?.unrealized_pnl_eur)}${pnlSub}</td>` +
            `<td class="num">${fmt(avgPointsOf(p))}${ptsSub}</td>` +
            `${ppmCell(p)}</tr>`
          );
        })
        .join("") +
      `</tbody></table></div>`
    );
  }
  function profileTradeRows(profile, onlyPlayer = null) {
    return arr(profile.trades)
      .filter((r) => !onlyPlayer || String(r.player_id) === String(onlyPlayer))
      .sort((a, b) => (Date.parse(b.at) || 0) - (Date.parse(a.at) || 0));
  }
  function tradeDirection(r, profile) {
    if (r.direction === "purchase") return "Kauf";
    if (r.direction === "sale") return "Verkauf";
    if (r.buyer === profile.name) return "Kauf";
    if (r.seller === profile.name) return "Verkauf";
    if (r.side === "buy" || r.direction === "purchase") return "Kauf";
    if (r.side === "sell" || r.direction === "sale") return "Verkauf";
    return "Transfer";
  }
  function profileTrades(profile, onlyPlayer = null, limit = null) {
    let trades = profileTradeRows(profile, onlyPlayer);
    if (!onlyPlayer && tradeFilter !== "all")
      trades = trades.filter(
"""
    '        (r) => tradeDirection(r, profile) === (tradeFilter === "buy" ? "Kauf" : "Verkauf")'
    ',\n'
    r"""      );
    if (limit) trades = trades.slice(0, limit);
    if (!trades.length) return empty("Keine beobachteten Transfers in dieser Auswahl.");
"""
    '    return `<div class="table-wrap"><table><thead><tr><th>Datum</th>${onlyPlayer ? "" : "<'
    'th>Spieler</th>"}<th>Vorgang</th><th class="num">Preis</th><th class="num">Damals MW</th><'
    'th class="num">Aufschlag</th></tr></thead><tbody>${trades\n'
    r"""      .map((r) => {
        const type = tradeDirection(r, profile),
          uncertain =
            r.update_boundary ||
            r.mw_unknown_reason ||
            r.mw_quality === "unknown" ||
            !["api_history", "db_history", "database_daily"].includes(r.mw_source) ||
            num(r.market_value) === null ||
            num(r.market_value) <= 0,
          reference = uncertain
            ? '—<small class="cell-sub">' +
              esc(r.update_boundary ? "Updatefenster" : "MW unsicher") +
              "</small>"
            : money(r.market_value);
"""
    '        return `<tr><td>${esc(date(r.at))}</td>${onlyPlayer ? "" : `<td><button class="tex'
    't-link" data-player="${esc(r.player_id)}" data-player-manager="${esc(profile.user_id)}">${'
    'esc(r.name)} ↗</button></td>`}<td>${chip(type)}</td><td class="num">${money(r.amount)}</td'
    '><td class="num">${reference}</td><td class="num">${type === "Kauf" && !uncertain ? pct(r.'
    'premium_pct) : "—"}</td></tr>`;\n'
    r"""      })
      .join("")}</tbody></table></div>`;
  }
  function resultBridge(profile) {
    const f = profile.summary || {},
      count = num(profile.coverage?.round_trips_with_decomposition),
      move = num(f.realized_market_value_change_eur),
      buy = num(f.realized_buy_premium_eur),
      sell = num(f.realized_sale_premium_eur),
      known = count !== null && count > 0 && [move, buy, sell].every((v) => v !== null),
      rows = [
        ["MW-Bewegung", move],
        ["− Kaufaufschläge", buy],
        ["+ Verkaufsaufschläge", sell],
      ],
      total = known ? move - buy + sell : null;
"""
    '    return `<div class="result-bridge" aria-label="Zusammensetzung des Handelsergebnisses"'
    '>${rows.map(([label, amount]) => `<div><span>${label}</span><strong>${money(known ? amount'
    ' : null)}</strong></div>`).join("")}<div class="bridge-total"><span>= Ergebnis der Teilmen'
    'ge</span><strong class="${cls(total)}">${money(total)}</strong></div></div>${note(known ? '
    'fmt(count) + " von " + fmt(f.complete_round_trips) + " Paaren mit vergleichbaren Marktwert'
    'en an Kauf und Verkauf." : "Für die Ergebnisbrücke fehlen vergleichbare Marktwerte an Kauf'
    ' und Verkauf.")}`;\n'
    r"""  }
  function profileResults(profile) {
    const pairs = arr(profile.round_trips);
"""
    '    if (!pairs.length) return empty("Noch keine vollständig belegten Kauf-Verkauf-Paare.")'
    ';\n'
    '    return `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th class="num">Kauf'
    '</th><th class="num">Verkauf</th><th class="num">Realisiert</th></tr></thead><tbody>${pair'
    's.map((r) => `<tr><td><button class="text-link" data-player="${esc(r.player_id)}" data-pla'
    'yer-manager="${esc(profile.user_id)}">${esc(r.name)} ↗</button></td><td class="num">${mone'
    'y(r.buy_amount)}</td><td class="num">${money(r.sell_amount)}</td><td class="num ${cls(r.pn'
    'l_eur)}">${money(r.pnl_eur)}</td></tr>`).join("")}</tbody></table></div>`;\n'
    r"""  }
  function cashFlowBars(profile) {
    const f = profile.summary || {},
      rows = [
        ["Käufe", f.purchase_spend_eur, "muted"],
        ["Verkäufe", f.sale_proceeds_eur, ""],
        [
          "Netto-Geldfluss",
          f.net_transfer_cashflow_eur,
          num(f.net_transfer_cashflow_eur) < 0 ? "danger" : "",
        ],
      ],
      scale = Math.max(...rows.map((r) => Math.abs(r[1] || 0)), 1);
"""
    '    return `<div class="budget-list">${rows.map(([label, value, tone]) => `<div class="bud'
    'get-row"><span>${label}</span><div class="budget-track"><div class="budget-fill ${tone}" s'
    'tyle="width:${num(value) === null ? 0 : (Math.abs(value) / scale) * 100}%"></div></div><st'
    'rong class="${label === "Netto-Geldfluss" ? cls(value) : ""}">${money(value)}</strong></di'
    'v>`).join("")}</div>`;\n'
    r"""  }
  function overviewGlobalMatches(roster) {
    const q = normSearch(query.trim());
    if (q.length < 2) return "";
    const rosterIds = new Set(roster.map((p) => String(p.pid))),
      matches = buildEfficiencyPool()
        .filter(
          (p) =>
            !rosterIds.has(String(p.pid)) &&
            (!position || String(p.position || "") === position) &&
            normSearch(`${p.search_name || ""} ${p.name || ""} ${p.team || ""}`).includes(q),
        )
        .sort((a, b) => (b.avgPerM ?? -1) - (a.avgPerM ?? -1) || b.mv - a.mv)
        .slice(0, 15);
    if (!matches.length) return "";
    return (
      `<div style="margin-top:22px"><div class="panel-head">` +
      `<div><h3>Treffer in der Bundesliga außerhalb dieses Kaders (${matches.length})</h3>` +
      `<p>Aus Liga, Transfermarkt und allen 18 Bundesliga-Vereinen.</p></div>` +
      `<button type="button" class="button secondary" data-view="spieler">` +
      `Im Preis-Leistungs-Radar öffnen →</button></div>` +
      `<div class="table-wrap"><table><thead><tr><th>Spieler</th>` +
      `<th>Verfügbarkeit &amp; Rolle</th><th class="num">Marktwert</th>` +
      `<th class="num">Punkteschnitt</th><th class="num">Pkt. / Mio. €</th>` +
      `</tr></thead><tbody>` +
      matches
        .map((p) => {
          const totSub =
            p.totalPts !== null
              ? `<small class="cell-sub">${fmt(p.totalPts)} Saison-Pkt.</small>`
              : "";
          return (
            `<tr><td>${playerName(p)}</td>` +
            `<td>${chip(p.badgeText, p.badgeTone)} ${predictedStatus(p)}` +
            `${jevBadgeInline(p)}</td><td class="num">${money(p.mv)}</td>` +
            `<td class="num">${fmt(p.avgPts)}${totSub}</td>` +
            `${ppmCell(p)}</tr>`
          );
        })
        .join("") +
      `</tbody></table></div></div>`
    );
  }
  function overviewSquadSection(filtered, roster = managerRoster()) {
    return overviewSquadTable(filtered) + overviewGlobalMatches(roster);
  }
  function overview() {
    const profile = currentProfile(),
      f = profile.summary || {},
      roster = managerRoster(profile),
      q = normSearch(query.trim()),
      filtered = roster.filter(
        (p) =>
          (!q ||
            normSearch(`${p.search_name || ""} ${p.name || ""} ${p.team || ""}`).includes(q)) &&
          (!position || String(p.position) === position),
      ),
      live = num(profile.live_points),
      complete = num(profile.completed_points);
    return (
      title(
        esc(profile.name) + (profile.me ? " · Mein Team" : ""),
        "Mannschaft, Punkte und Transfers. Für jeden Manager dieselbe Sicht.",
      ) +
      managerSwitcher() +
"""
    '      `<div class="metrics">${metric("Abgeschlossene Saisonpunkte", fmt(complete), live !='
    '= null ? "Live inklusive laufendem Spieltag: " + fmt(live) : "Laufender Spieltag noch nich'
    't erfasst")}${metric("Kaderwert", money(f.squad_value_eur), roster.length + " Spieler · Ko'
    'nto " + (num(f.budget_eur) === null ? "nicht einsehbar" : money(f.budget_eur)))}${metric("'
    'Realisierter Gewinn / Verlust", money(f.realized_pnl_eur), fmt(f.complete_round_trips) + "'
    ' belegte Kauf-Verkauf-Paare", cls(f.realized_pnl_eur))}${metric("Nicht realisierter Gewinn'
    ' / Verlust", money(f.unrealized_pnl_eur), fmt(f.holdings_with_cost_basis) + " gehaltene Sp'
    'ieler mit bekanntem Einstand", cls(f.unrealized_pnl_eur))}</div>\n'
    '      <div class="overview-charts"><article class="panel"><div class="panel-head"><h2>Sais'
    'onverlauf</h2><div class="segmented"><button data-points-mode="total" class="${pointsMode '
    '=== "total" ? "active" : ""}">Gesamt</button><button data-points-mode="matchday" class="${'
    'pointsMode === "matchday" ? "active" : ""}">Je Spieltag</button></div></div><div id="point'
    's-history">${pointHistory(profile)}</div></article><article class="panel"><div class="pane'
    'l-head"><h2>Handelsverlauf</h2><div class="segmented"><button data-finance-mode="profit" c'
    'lass="${financeMode === "profit" ? "active" : ""}">Gewinn</button><button data-finance-mod'
    'e="cashflow" class="${financeMode === "cashflow" ? "active" : ""}">Geldfluss</button></div'
    '></div><div id="financial-history">${financialHistory(profile)}</div></article></div>\n'
    '      <article class="panel quiet"><div class="panel-head"><div><h2>Mannschaft</h2><p>Ein '
    'Spieler öffnet Leistung, Marktwert und seine Transfers bei ${esc(profile.name)} — die Such'
    'e findet auch alle übrigen Bundesliga-Spieler.</p></div>'
    '${chip(roster.length + " Spieler")}</div><div class="toolbar"><input type="search" id="pla'
    'yer-search" placeholder="Fußballer in Kader & Bundesliga suchen" aria-label="Fußballer im '
    'gewählten Kader oder in der Bundesliga suchen" value="${esc(query)}"><select id="position-'
    'filter" aria-label="Position filtern"><option value="">Alle Positionen</option>${[\n'
    r"""        [1, "Tor"],
        [2, "Abwehr"],
        [3, "Mittelfeld"],
        [4, "Angriff"],
      ]
        .map(
          ([v, l]) =>
"""
    '            `<option value="${v}" ${position === String(v) ? "selected" : ""}>${l}</option'
    '>`,\n'
    r"""        )
        .join(
          "",
"""
    '        )}</select><button type="button" class="button secondary" data-view="spieler">Alle'
    ' Bundesliga-Spieler →</button></div><div id="squad-table">${overviewSquadSection(filtered,'
    ' roster)}</div>${note("Einstand und Ergebnisse nur aus belegten Käufen. — bedeutet unbekan'
    'nt.")}</article>\n'
    '      <div class="overview-charts">${panel("Käufe & Verkäufe", "Geldbewegungen im beobacht'
    'eten Zeitraum.", `<div class="two-stats"><div><div class="compact-stat">${fmt(f.purchases)'
    '}</div><div class="compact-label">Käufe</div></div><div><div class="compact-stat">${fmt(f.'
    'sales)}</div><div class="compact-label">Verkäufe</div></div></div>${cashFlowBars(profile)}'
    '${note("Verkaufserlöse sind kein Gewinn. Unbekannte Startkaderkosten bleiben offen.")}`)}$'
    '{panel("Bezahlte Aufschläge", "Verglichen mit dem damaligen Marktwert.", `<div class="two-'
    'stats"><div><div class="compact-stat">${money(f.premium_eur)}</div><div class="compact-lab'
    'el">Summe belegter Aufschläge</div></div><div><div class="compact-stat">${pct(f.median_pre'
    'mium_pct)}</div><div class="compact-label">Median pro Kauf</div></div></div>${note(fmt(f.p'
    'riced_purchases) + " vergleichbare Käufe. Marktwertminimum und Updatefenster gesondert prü'
    'fen.")}<div class="rule-line"></div><div class="count-row"><span>Verkäufe ohne bekannten E'
    'instand</span><strong>${fmt(f.unknown_cost_basis_sales)}</strong></div>`)}</div>\n'
    '      <article class="panel"><div class="panel-head"><h2>Abgeschlossene Geschäfte</h2>${ch'
    'ip(fmt(f.complete_round_trips) + " Paare")}</div>${resultBridge(profile)}${profileResults('
    'profile)}</article>\n'
    '      <article class="panel quiet"><div class="panel-head"><div><h2>Alle beobachteten Tran'
    'sfers</h2><p>Die Historie bleibt beim gewählten Manager.</p></div><div class="segmented">$'
    '{[\n'
    r"""        ["all", "Alle"],
        ["buy", "Käufe"],
        ["sell", "Verkäufe"],
      ]
        .map(
          ([v, l]) =>
"""
    '            `<button data-trade-filter="${v}" class="${tradeFilter === v ? "active" : ""}"'
    '>${l}</button>`,\n'
    r"""        )
        .join(
          "",
"""
    '        )}</div></div><div id="profile-transfers">${profileTrades(profile)}</div>${note("Z'
    'eitfenster und Belegqualität begrenzen die Auswertung. Keine Schätzung verborgener Rivalen'
    'gebote.")}</article>`\n'
    r"""    );
  }
  function planStatus(status) {
    const labels = {
      offen: "Offen",
      in_pruefung: "In Prüfung",
      executed: "Ausgeführt",
      completed: "Erledigt",
      pending: "Vorgeschlagen",
      proposed: "Vorgeschlagen",
      planned: "Geplant",
      ready: "Bereit",
      blocked: "Noch blockiert",
      waiting: "Wartet",
      monitor: "Beobachten",
      review: "Prüfen",
    };
    return chip(
      labels[status] || status || "Vorschlag",
      ["executed", "completed"].includes(status)
        ? "good"
        : ["blocked", "waiting"].includes(status)
          ? "warn"
          : "",
    );
  }
  function actionHref(value) {
    const s = String(value || "");
    return /^#(?:heute|uebersicht|liga|spieler|jev|regeln)(?:\?manager=[a-zA-Z0-9_-]+)?$/.test(s)
      ? s
      : /^https?:\/\//i.test(s)
        ? safeURL(s)
        : "";
  }
  function todayKPIsHTML() {
    if (cash === null && !squad.length && !market.length) return "";
    const planIds = arr(teamPlan.lineup_ids),
      elfFilled = lineupCount ?? (planIds.length || squad.filter((p) => p.elf).length),
      formStr = D.formation || teamPlan.formation || "11er-Elf",
      jev7d = num(JEV.zahlen?.urteile_7d),
      folgen7d = num(JEV.zahlen?.folgen_7d) ?? 0,
      liveSt = ["planned", "ready", "executing"],
      plannedBuys = liveIntents.filter(
        (i) => i.kind === "bid" && liveSt.includes(i.status),
      ).length,
      cashSub = bidTotal ? "Offene Gebote: " + money(bidTotal) : "Anpfiff-Deckung gesichert",
      buyLabel = plannedBuys ? plannedBuys + " Kauf geplant" : "Bereit",
      elfSub = `Formation ${esc(formStr)} · ${buyLabel}`,
      jevVal = jev7d !== null ? fmt(jev7d) + " Checks" : "Aktiv",
      jevSub =
        jev7d !== null
          ? `Letzte 7 Tage · ${fmt(folgen7d)} Eingriffe`
          : "News- & Rollen-Analyse";
    return `<div class="metrics">
      ${metric("Freies Konto", money(cash), cashSub, cls(cash))}
      ${metric("Startelf-Status", `${fmt(elfFilled)} / 11`, elfSub)}
      ${metric("Kaderwert", money(teamValue), `${squad.length} Spieler im eigenen Kader`)}
      ${metric("Jev KI-Wächter", jevVal, jevSub)}
    </div>`;
  }
  function intentKindLabel(k) {
    if (k === "bid") return "Kauf-Gebot";
    if (k === "sell") return "Sofort-Verkauf";
    if (k === "list") return "Markt-Listing";
    return k || "Vorhaben";
  }
  function intentStatusChip(i) {
    const st = i?.status || "planned";
    if (st === "planned") {
      const sLeft = num(i.seconds_left);
      return chip(
        sLeft !== null && sLeft <= 0
          ? "Sofort fällig (nächster Takt)"
          : "Veto-Fenster offen",
        "blue",
      );
    }
    if (st === "ready" || st === "executing") return chip("Bereit für Executor", "good");
    if (st === "vetoed") return chip("Per Veto gestoppt", "bad");
    if (st === "executed") return chip("Umgesetzt", "good");
    return chip(st, "");
  }
  function controlHubHTML() {
    const plan = S.action_plan || {},
      steps = arr(plan.steps),
      active = liveIntents.filter((i) =>
        ["planned", "ready", "executing"].includes(i.status),
      ),
      recentHandled = liveIntents
        .filter((i) => ["vetoed", "executed"].includes(i.status))
        .slice(0, 3);
    const cardsHTML = active.length
      ? `<div class="intent-grid">${active
          .map((i) => {
            const p = players.get(String(i.player_id || "")) || {
                pid: i.player_id,
                name: i.player_name || "Spieler",
                team: i.club || "",
                pos: i.pos || "",
                mv: i.amount,
              },
              adj =
                num(i.adjusted_from) !== null
                  ? `<small>Angepasst von ${money(i.adjusted_from)}</small>`
                  : `<small>ID #${esc(i.id)} · ${esc(intentKindLabel(i.kind))}</small>`;
            return (
              `<div class="intent-card" data-intent-card="${esc(i.id)}">` +
              `<div class="intent-head"><div class="intent-title">` +
              `${chip(intentKindLabel(i.kind), i.kind === "bid" ? "good" : "warn")}` +
              `${intentStatusChip(i)}` +
              `<button type="button" class="text-link"` +
              ` data-player="${esc(p.pid || i.player_id)}">` +
              `<strong>${esc(i.player_name || p.name || "Spieler")}</strong> ↗</button>` +
              `${aiFactorBadge(p)}</div>` +
              `<div class="intent-amount">${money(i.amount)}${adj}</div></div>` +
              (i.rationale
                ? `<p class="intent-reason">${esc(i.rationale)}</p>`
                : "") +
              intentActionsHTML(i) +
              `</div>`
            );
          })
          .join("")}</div>`
      : "";
    const recentHTML = recentHandled.length
      ? `<div class="factor-chips" style="margin-top:8px">${recentHandled
          .map(
            (i) =>
              `<span class="factor-chip">#${esc(i.id)} ${esc(
                i.player_name || "Vorhaben",
              )}: ${i.status === "vetoed" ? "Veto gespeichert" : "Umgesetzt"} (${money(
                i.amount,
              )})</span>`,
          )
          .join("")}</div>`
      : "";
    const stepsHTML = steps.length
      ? steps
          .map((s, i) => {
            const href = actionHref(s.href),
              priceTxt =
                num(s.amount_eur) !== null
                  ? `<strong class="step-amount">${money(s.amount_eur)}</strong>`
                  : num(s.reference_price_eur) !== null
                    ? `<p>Marktpreis zur Prüfung: ${money(s.reference_price_eur)}</p>`
                    : "";
            return (
              `<div class="action-step">` +
              `<span class="action-number">${String(i + 1).padStart(2, "0")}</span>` +
              `<div><div class="step-heading"><h3>${esc(s.title)}</h3>` +
              `${planStatus(s.status)}</div>` +
              (s.detail ? `<p>${esc(s.detail)}</p>` : "") +
              priceTxt +
              `<div class="step-actions">` +
              (s.player_id
                ? `<button class="text-link" data-player="${esc(s.player_id)}"` +
                  ` data-player-manager="${esc(my.user_id || "")}">Spieler ansehen ↗</button>`
                : "") +
              (href
                ? `<a class="text-link" href="${esc(href)}">${esc(
                    s.action_label || "Details ansehen",
                  )} ↗</a>`
                : "") +
              `</div></div></div>`
            );
          })
          .join("")
      : !active.length
        ? empty("Keine offenen Vorhaben im Veto-Fenster. Planner prüft alle 10 Min. neu.")
        : "";
    return (
      `<article class="panel control-hub action-plan" id="control-hub">` +
      `<div class="panel-head"><div>` +
      `<span class="eyebrow">KI-Steuerzentrale · Direkte Backend-Anbindung</span>` +
      `<h2>${esc(plan.title || "Der nächste Plan wird geprüft.")}</h2>` +
      `<p>${esc(
        plan.summary || "Direkte Steuerung der geplanten Züge auf der Box.",
      )}</p></div><div class="hub-actions">` +
      `${planStatus(plan.status)}` +
      `<button type="button" class="button secondary btn-sm" data-backend-act="sync">` +
      `Live-Sync</button>` +
      `<button type="button" class="button secondary btn-sm" data-backend-act="refresh">` +
      `↻ Neu berechnen</button></div></div>` +
      (lastBackendMsg
        ? `<div class="ai-verdict good" style="margin-bottom:10px">` +
          `<strong>Backend:</strong> <span id="backend-feedback">${esc(
            lastBackendMsg,
          )}</span></div>`
        : `<span id="backend-feedback" hidden></span>`) +
      cardsHTML +
      recentHTML +
      stepsHTML +
      (liveProtocol.length
        ? `<details class="backend-guide">` +
          `<summary><span>Protokoll (${liveProtocol.length} Einträge)</span>` +
          `<span class="tag">Aufklappen ↓</span></summary>` +
          `<div class="factor-chips" style="margin-top:8px">${liveProtocol
            .slice(0, 8)
            .map(
              (e) =>
                `<span class="factor-chip">${esc(e.kind_label || e.action || "Aktion")}: ${esc(
                  e.player || e.player_name || "",
                )} ${num(e.price ?? e.amount) !== null ? money(e.price ?? e.amount) : ""} (${esc(
                  e.status_label || e.status || "",
                )})</span>`,
            )
            .join("")}</div></details>`
        : "") +
      `<span hidden>POST /api/intents/{id}/execute-now</span>` +
      `</article>`
    );
  }
  function marketRadarHTML() {
    if (!market.length) return "";
    const items = [...market]
      .sort((a, b) => {
        const aSel = purchaseReviews[String(a.pid)]?.selected ? 1 : 0,
          bSel = purchaseReviews[String(b.pid)]?.selected ? 1 : 0;
        if (aSel !== bSel) return bSel - aSel;
        return (
          aiFactor(b).score - aiFactor(a).score ||
          (num(b.score) ?? 0) - (num(a.score) ?? 0) ||
          (num(b.mv24) ?? 0) - (num(a.mv24) ?? 0)
        );
      })
      .slice(0, 8);
    return (
      `<article class="panel"><div class="panel-head"><div>` +
      `<span class="eyebrow">Transfermarkt-Radar</span>` +
      `<h2>Top-Kandidaten nach AI-Faktor</h2>` +
      `<p>Klick auf einen Spieler öffnet die 4-Säulen-Begründung.</p></div>` +
      `${chip(market.length + " auf dem Markt")}</div>` +
      `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th>AI-Faktor (0–100)</th>` +
      `<th>Rolle &amp; Jev</th><th class="num">Marktwert</th>` +
      `<th class="num">Pkt. / Mio. €</th>` +
      `<th>KI-Urteil</th></tr></thead><tbody>` +
      items
        .map((p) => {
          const v = agentVerdictForPlayer(p),
            mvSub =
              num(p.mv24) !== null && p.mv24 !== 0
                ? `<small class="cell-sub ${cls(p.mv24)}">${money(p.mv24)} / 24h</small>`
                : "";
          return (
            `<tr><td>${playerName(p)}</td>` +
            `<td>${aiFactorCell(p)}</td>` +
            `<td>${predictedStatus(p)}${jevBadgeInline(p)}</td>` +
            `<td class="num">${money(p.mv)}${mvSub}</td>` +
            `${ppmCell(p)}` +
            `<td>${chip(v.badge, v.tone)}</td></tr>`
          );
        })
        .join("") +
      `</tbody></table></div></article>`
    );
  }
  const marketIds = new Set(market.map((p) => String(p.pid))),
    ligaByPid = new Map();
  for (const m of arr(D.manager_squads)) {
    for (const sp of arr(m.spieler)) {
      const id = String(sp.pid || "");
      if (id)
        ligaByPid.set(id, {
          user_id: String(m.user_id || ""),
          name: m.name || "Manager",
          me: !!m.me,
          in_elf: !!sp.in_elf,
        });
    }
  }
  function buildEfficiencyPool() {
    const inPlan = new Set(arr(teamPlan.lineup_ids).map(String)),
      out = [];
    for (const [pid, p] of players.entries()) {
      const mv = num(p.mv);
      if (mv === null || mv <= 0) continue;
      const inOwn = ownIds.has(pid),
        onMkt = marketIds.has(pid),
        ligaOwn = ligaByPid.get(pid) || null,
        inLiga = inOwn || !!ligaOwn,
        avgPts = avgPointsOf(p),
        totalPts = totalPointsOf(p),
        forecastPts = forecastPointsOf(p),
        hasMetrics = avgPts !== null || totalPts !== null || forecastPts !== null;
      const isElf = inOwn && (inPlan.has(pid) || !!p.elf),
        badgeText = inOwn
          ? isElf
            ? "Mein Kader · Startelf"
            : "Mein Kader · Bank"
          : onMkt
            ? p.listing_type === "manager"
              ? "Am Markt · Manager"
              : "Am Markt · Kickbase"
            : ligaOwn && !ligaOwn.me
              ? "Liga · " + ligaOwn.name
              : "Bundesliga · frei",
        badgeTone = inOwn
          ? isElf
            ? "good"
            : "blue"
          : onMkt
            ? "good"
            : ligaOwn && !ligaOwn.me
              ? "warn"
              : "",
        posNum = num(p.position) ?? (posCodeMap[p.pos] || null);
      out.push({
        ...p,
        pid,
        mv,
        position: posNum,
        inOwn,
        onMkt,
        inLiga,
        isElf,
        ligaOwn,
        hasMetrics,
        badgeText,
        badgeTone,
        avgPts,
        totalPts,
        forecastPts,
        aiFactorScore: aiFactor(p).score,
        avgPerM: ptsPerMio(avgPts, mv),
        totalPerM: ptsPerMio(totalPts, mv),
        forecastPerM: ptsPerMio(forecastPts, mv),
      });
    }
    return out;
  }
  function effSortValue(item, key = effSort) {
    if (key === "ai_factor") return item.aiFactorScore;
    if (key === "total_per_m") return item.totalPerM;
    if (key === "forecast_per_m") return item.forecastPerM;
    if (key === "mv") return item.mv;
    return item.avgPerM;
  }
  function efficiencyFiltered(all = buildEfficiencyPool()) {
    const q = normSearch(effQuery.trim());
    return all
      .filter((p) => {
        if (!q && !p.hasMetrics && !p.inOwn && !p.onMkt) return false;
        if (effPool === "kader" && !p.inOwn) return false;
        if (effPool === "market" && !p.onMkt) return false;
        if (effPool === "liga" && !p.inLiga) return false;
        if (effPos && String(p.position || "") !== effPos) return false;
        if (q) {
          const hay = normSearch(
            `${p.search_name || ""} ${p.name || ""} ${p.team || ""} ${p.pos || ""} ${
              p.badgeText || ""
            }`,
          );
          if (!hay.includes(q)) return false;
        }
        return true;
      })
      .sort((a, b) => {
        const av = effSortValue(a),
          bv = effSortValue(b);
        if (av === null && bv === null) return (b.avgPts ?? -1) - (a.avgPts ?? -1) || a.mv - b.mv;
        if (av === null) return 1;
        if (bv === null) return -1;
        return bv - av || (b.avgPts ?? -1) - (a.avgPts ?? -1) || a.mv - b.mv;
      });
  }
  function efficiencySummaryHTML(items) {
    const pickBest = (field) =>
        [...items]
          .filter((x) => num(x[field]) !== null)
          .sort((a, b) => b[field] - a[field] || a.mv - b.mv)[0] || null,
      bAvg = pickBest("avgPerM"),
      bTot = pickBest("totalPerM"),
      bFc = pickBest("forecastPerM");
    const cell = (label, item, ratioKey, ptsKey, unitTxt) => {
      if (!item) {
        return (
          `<div class="forecast-cell"><span>${esc(label)}</span>` +
          `<strong>—</strong><small>Keine Daten in dieser Auswahl</small></div>`
        );
      }
      return (
        `<div class="forecast-cell"><span>${esc(label)}</span>` +
        `<strong class="positive">${esc(item.name)} · ${fmtRatio(item[ratioKey])} / Mio.</strong>` +
        `<small>${fmt(item[ptsKey], 0)} ${esc(unitTxt)} bei ${money(item.mv)}` +
        ` · ${esc(item.badgeText)}</small></div>`
      );
    };
    return (
      `<div class="forecast-strip eff-strip">` +
      cell("Top Ø-Punkte / Mio. €", bAvg, "avgPerM", "avgPts", "Ø-Pkt.") +
      cell("Top Saisonpunkte / Mio. €", bTot, "totalPerM", "totalPts", "Saison-Pkt.") +
      cell("Top 4-ST-Prognose / Mio. €", bFc, "forecastPerM", "forecastPts", "Prog.-Pkt.") +
      `</div>`
    );
  }
  function efficiencyBodyHTML() {
    const items = efficiencyFiltered();
    if (!items.length) {
      return empty("Keine Spieler mit Marktwert und Punktedaten für diese Filterauswahl.");
    }
    const limit = view === "spieler" ? 30 : 15,
      shown = effShowAll ? items : items.slice(0, limit),
      thBtn = (key, label) => {
        const on = effSort === key;
        return (
          `<th class="num" aria-sort="${on ? "descending" : "none"}">` +
          `<button type="button" class="th-sort ${on ? "active" : ""}"` +
          ` data-eff-sort="${key}">${esc(label)}${on ? " ↓" : ""}</button></th>`
        );
      };
    return (
      efficiencySummaryHTML(items) +
      `<div class="table-wrap eff-table-wrap" tabindex="0" role="region"` +
      ` aria-labelledby="eff-table-heading"><table class="eff-table"><thead><tr>` +
      `<th>Spieler</th><th>Verfügbarkeit &amp; AI-Faktor</th>` +
      thBtn("mv", "Marktwert") +
      thBtn("avg_per_m", "Ø Pkt. / Mio. €") +
      thBtn("total_per_m", "Gesamt / Mio. €") +
      thBtn("forecast_per_m", "4-ST-Prog. / Mio. €") +
      `</tr></thead><tbody>` +
      shown
        .map((p) => {
          const mvSub =
              num(p.mv24) !== null && p.mv24 !== 0
                ? `<small class="cell-sub ${cls(p.mv24)}">${money(p.mv24)} / 24h</small>`
                : "",
            roleChips =
              p.inOwn || p.onMkt || (p.status && p.status !== "unknown") || p.kb_prob
                ? " " + predictedStatus(p) + jevBadgeInline(p)
                : "",
            avgCls = [
              effTone(p.avgPerM, "avg"),
              effSort === "avg_per_m" ? "eff-active-val" : "",
            ]
              .filter(Boolean)
              .join(" "),
            totCls = [
              effTone(p.totalPerM, "total"),
              effSort === "total_per_m" ? "eff-active-val" : "",
            ]
              .filter(Boolean)
              .join(" "),
            fcCls = [
              effTone(p.forecastPerM, "forecast"),
              effSort === "forecast_per_m" ? "eff-active-val" : "",
            ]
              .filter(Boolean)
              .join(" ");
          return (
            `<tr><th>${playerName(p)}</th>` +
            `<td>${aiFactorBadge(p)} ${chip(p.badgeText, p.badgeTone)}${roleChips}</td>` +
            `<td class="num">${money(p.mv)}${mvSub}</td>` +
            `<td class="num"><strong class="${avgCls}">${fmtRatio(p.avgPerM)}</strong>` +
            `<small class="cell-sub">${
              p.avgPts !== null ? "Ø " + fmt(p.avgPts) + " Pkt." : "Ø offen"
            }</small></td>` +
            `<td class="num"><strong class="${totCls}">${fmtRatio(p.totalPerM)}</strong>` +
            `<small class="cell-sub">${
              p.totalPts !== null ? fmt(p.totalPts) + " Pkt." : "Saison offen"
            }</small></td>` +
            `<td class="num"><strong class="${fcCls}">${fmtRatio(p.forecastPerM)}</strong>` +
            `<small class="cell-sub">${
              p.forecastPts !== null ? fmt(p.forecastPts, 0) + " Pkt. (4 ST)" : "Nur Kader/Markt"
            }</small></td></tr>`
          );
        })
        .join("") +
      `</tbody></table></div>` +
      (items.length > limit
        ? `<div class="eff-more-wrap"><button type="button" class="button secondary"` +
          ` id="eff-toggle-all">${
            effShowAll
              ? "Auf Top " + limit + " reduzieren"
              : "Alle " + fmt(items.length) + " Spieler anzeigen"
          }</button></div>`
        : "") +
      note(
        "Verhältnis = Punkte ÷ (aktueller Marktwert in Mio. €). " +
          "Beobachtete Schnitte, Saison-Gesamtpunkte und 4-Spieltage-Prognose bleiben getrennt.",
      )
    );
  }
  function efficiencyRadarHTML(fullPage = false) {
    const all = buildEfficiencyPool();
    if (!all.length) return "";
    const filteredCount = efficiencyFiltered(all).length,
      pools = [
        ["all", "Ganze Bundesliga"],
        ["liga", "Liga"],
        ["market", "Transfermarkt"],
        ["kader", "Mein Kader"],
      ],
      sorts = [
        ["avg_per_m", "Ø-Pkt. / Mio. €"],
        ["total_per_m", "Gesamt / Mio. €"],
        ["forecast_per_m", "4-ST-Prog. / Mio. €"],
        ["ai_factor", "AI-Faktor"],
      ],
      headRight = `<span class="tag" id="eff-count-chip">${fmt(filteredCount)} Spieler</span>`,
      inner =
        `<div class="panel-head"><div>` +
        `<span class="eyebrow">Preis-Leistungs-Radar &amp; Bundesliga-Suche</span>` +
        `<h2 id="eff-table-heading">Punkte pro Mio. € Marktwert</h2>` +
        `<p>Jeden Bundesliga-Spieler suchen und Punkte pro Mio. € vergleichen.</p></div>` +
        `<div>${headRight}</div></div>` +
        `<div class="eff-controls"><div class="toolbar eff-toolbar">` +
        `<input type="search" id="eff-search"` +
        ` placeholder="Jeden Bundesliga-Spieler oder Verein suchen (z. B. Kane, Olise, Bayern …)"` +
        ` aria-label="Spieler in der Preis-Leistungs-Tabelle suchen" value="${esc(effQuery)}">` +
        `<select id="eff-pos" aria-label="Position in der Preis-Leistungs-Tabelle filtern">` +
        `<option value="">Alle Positionen</option>${[
          [1, "Tor"],
          [2, "Abwehr"],
          [3, "Mittelfeld"],
          [4, "Angriff"],
        ]
          .map(
            ([v, l]) =>
              `<option value="${v}" ${effPos === String(v) ? "selected" : ""}>${l}</option>`,
          )
          .join("")}</select></div>` +
        `<div class="eff-filter-row">` +
        `<div class="segmented" role="group" aria-label="Spielerkreis filtern">${pools
          .map(
            ([k, l]) =>
              `<button type="button" data-eff-pool="${k}"` +
              ` class="${effPool === k ? "active" : ""}"` +
              ` aria-pressed="${effPool === k}">${l}</button>`,
          )
          .join("")}</div>` +
        `<div class="segmented" role="group" aria-label="Kennzahl für Sortierung wählen">${sorts
          .map(
            ([k, l]) =>
              `<button type="button" data-eff-sort="${k}"` +
              ` class="${effSort === k ? "active" : ""}"` +
              ` aria-pressed="${effSort === k}">${l}</button>`,
          )
          .join("")}</div></div></div>` +
        `<div id="eff-body">${efficiencyBodyHTML()}</div>`;
    return `<article class="panel" id="efficiency-radar">${inner}</article>`;
  }
  function spielerView() {
    return (
      title(
        "Alle Spieler · Preis-Leistungs-Radar",
        "Jeden Bundesliga-Spieler suchen und Punkte pro Mio. € Marktwert vergleichen.",
      ) + efficiencyRadarHTML(true)
    );
  }
  function updateEfficiencyView() {
    const body = document.getElementById("eff-body");
    if (body) body.innerHTML = efficiencyBodyHTML();
    const chipEl = document.getElementById("eff-count-chip");
    if (chipEl) chipEl.textContent = fmt(efficiencyFiltered().length) + " Spieler";
    document.querySelectorAll("[data-eff-pool]").forEach((x) => {
      const on = x.dataset.effPool === effPool;
      x.classList.toggle("active", on);
      x.setAttribute("aria-pressed", String(on));
    });
    document.querySelectorAll("[data-eff-sort]").forEach((x) => {
      const on = x.dataset.effSort === effSort;
      x.classList.toggle("active", on);
      if (x.closest && x.closest(".segmented")) x.setAttribute("aria-pressed", String(on));
    });
  }
  function lineupPitchHTML() {
    if (!squad.length) return "";
    const inPlan = new Set(arr(teamPlan.lineup_ids).map(String));
    let elf = squad.filter((p) => inPlan.has(String(p.pid)) || p.elf);
    if (!elf.length) {
      elf = [...squad]
        .sort((a, b) => aiFactor(b).score - aiFactor(a).score)
        .slice(0, 11);
    }
    const elfIds = new Set(elf.map((p) => String(p.pid))),
      bench = squad.filter((p) => !elfIds.has(String(p.pid))),
      posOf = (p) => num(p.position) ?? (posCodeMap[p.pos] || 3),
      rowY = { 1: 545, 2: 425, 3: 280, 4: 135 },
      statusClsOf = (p) => {
        if (p?.status === "out" || p?.fit === false) return "pp-out";
        if (p?.status === "starter" || p?.kb_prob === 1) return "pp-starter";
        if (p?.status === "bench" || (p?.kb_prob && p.kb_prob >= 2)) return "pp-bench";
        return "pp-unknown";
      },
      lastName = (n) => {
        const parts = String(n || "?").trim().split(/\s+/);
        return parts[parts.length - 1] || n || "?";
      },
      chips = [];
    for (const posId of [1, 2, 3, 4]) {
      const row = elf.filter((p) => posOf(p) === posId);
      row.forEach((p, j) => {
        const x = (10 + (440 * (j + 0.5)) / row.length).toFixed(1),
          y = rowY[posId],
          sCls = statusClsOf(p),
          sc = aiFactor(p).score,
          img = safeURL(p.img),
          pid = esc(p.pid),
          cid = `pc-${pid}-${posId}-${j}`;
        if (img) {
          chips.push(
            `<g class="pp ${sCls}" data-player="${pid}" tabindex="0"` +
              ` role="button" aria-label="${esc(p.name)}" transform="translate(${x},${y})">` +
              `<circle class="pp-ring" r="20"/>` +
              `<clipPath id="${cid}"><circle r="17"/></clipPath>` +
              `<rect class="pp-inner" x="-17" y="-17" width="34" height="34" rx="17"/>` +
              `<image href="${esc(img)}" x="-17" y="-17" width="34" height="34"` +
              ` clip-path="url(#${cid})" preserveAspectRatio="xMidYMin slice"/>` +
              `<rect class="pp-badge" x="8" y="-24" width="26" height="13" rx="4"/>` +
              `<text class="pp-score-s" x="21" y="-14.5">${sc}</text>` +
              `<text class="pp-name" y="36">${esc(lastName(p.name))}</text></g>`,
          );
        } else {
          chips.push(
            `<g class="pp ${sCls}" data-player="${pid}" tabindex="0"` +
              ` role="button" aria-label="${esc(p.name)}" transform="translate(${x},${y})">` +
              `<circle class="pp-ring" r="18"/>` +
              `<text class="pp-score" y="4">${sc}</text>` +
              `<text class="pp-name" y="34">${esc(lastName(p.name))}</text></g>`,
          );
        }
      });
    }
    const r2 = elf.filter((p) => posOf(p) === 2).length,
      r3 = elf.filter((p) => posOf(p) === 3).length,
      r4 = elf.filter((p) => posOf(p) === 4).length,
      formStr = D.formation || teamPlan.formation || `${r2}-${r3}-${r4}`,
      stripes = [1, 3, 5]
        .map(
          (i) =>
            `<rect class="pitch-stripe" x="10" y="${10 + i * 96}" width="440" height="96"/>`,
        )
        .join(""),
      lines =
        `<rect class="pitch-line" x="10" y="10" width="440" height="580"/>` +
        `<line class="pitch-line" x1="10" y1="300" x2="450" y2="300"/>` +
        `<circle class="pitch-line" cx="230" cy="300" r="50"/>` +
        `<circle class="pitch-spot" cx="230" cy="300" r="3"/>` +
        `<rect class="pitch-line" x="100" y="10" width="260" height="90"/>` +
        `<rect class="pitch-line" x="160" y="10" width="140" height="36"/>` +
        `<rect class="pitch-line" x="100" y="500" width="260" height="90"/>` +
        `<rect class="pitch-line" x="160" y="554" width="140" height="36"/>`,
      benchHTML = bench.length
        ? `<div class="bench-strip"><span class="bench-label">Bank (${bench.length})</span>` +
          bench
            .map(
              (p) =>
                `<button type="button" class="bench-pill" data-player="${esc(p.pid)}">` +
                `<strong>${esc(lastName(p.name))}</strong>` +
                `<span class="muted">${esc(p.pos || "")} · AI ${aiFactor(p).score}</span>` +
                `</button>`,
            )
            .join("") +
          `</div>`
        : "";
    return (
      `<article class="panel" id="lineup-pitch"><div class="panel-head"><div>` +
      `<span class="eyebrow">Kaderaufstellung</span>` +
      `<h2>Empfohlene Elf (${esc(formStr)})</h2></div>` +
      `${chip(elf.length + " Startelf · " + bench.length + " Bank")}</div>` +
      `<div class="pitch-wrap">` +
      `<svg class="pitch" viewBox="0 0 460 600" role="img"` +
      ` aria-label="Empfohlene Elf (${esc(formStr)})">` +
      `<rect class="pitch-grass" x="10" y="10" width="440" height="580"/>` +
      `${stripes}${lines}${chips.join("")}</svg></div>` +
      `<div class="pitch-legend">` +
      `<span><i style="background:var(--positive)"></i>Startelf</span>` +
      `<span><i style="background:var(--warn)"></i>Wackelt / Bank</span>` +
      `<span><i style="background:var(--bad)"></i>Ausfall</span>` +
      `<span><i style="background:var(--faint)"></i>Offen</span>` +
      `<span>· Badge = AI-Faktor</span></div>` +
      benchHTML +
      `</article>`
    );
  }
  function jevView() {
    const z = JEV.zahlen || {},
      b = JEV.bilanz || {},
      feedRows = [...arr(JEV.feed)];
    if (!feedRows.length && JEV.spieler) {
      for (const [pid, entries] of Object.entries(JEV.spieler)) {
        for (const e of arr(entries)) {
          feedRows.push({
            ...e,
            player_id: e.player_id || pid,
            player: e.player || players.get(String(pid))?.name || "Spieler",
          });
        }
      }
    }
    const u7 = num(z.urteile_7d) ?? feedRows.length,
      f7 = num(z.folgen_7d) ?? 0,
      quote =
        num(b.treffer_quote_pct) !== null
          ? pct(b.treffer_quote_pct)
          : num(b.richtig) !== null && num(b.gesamt)
            ? `${fmt(b.richtig)} / ${fmt(b.gesamt)}`
            : "Aktiv",
      watched = Object.keys(JEV.spieler || {}).length;
    const rowsHTML = feedRows.length
      ? `<div class="table-wrap"><table><thead><tr><th>Spieler</th><th>Prüfung</th>` +
        `<th>KI-Urteil</th><th>Erklärung</th><th>Quelle</th></tr></thead><tbody>` +
        feedRows
          .slice(0, 40)
          .map((j) => {
            const tr = jevTranslate(j),
              pid = String(j.player_id || ""),
              pObj = players.get(pid),
              pCell = pObj
                ? playerName(pObj)
                : `<strong>${esc(j.player || j.player_name || "Meldung")}</strong>`,
              srcTxt = tr.subject
                ? `„${esc(tr.subject)}“ (${esc(tr.source)})`
                : esc(tr.source || date(tr.at));
            return (
              `<tr><td>${pCell}</td>` +
              `<td>${esc(tr.kindLabel)}</td>` +
              `<td>${chip(tr.title, tr.tone)}</td>` +
              `<td>${esc(tr.explanation)}</td>` +
              `<td class="muted">${srcTxt}</td></tr>`
            );
          })
          .join("") +
        `</tbody></table></div>`
      : empty("Aktuell liegen keine Jev-Meldungen im Zeitfenster vor.");
    return (
      title(
        "Jev-Analyse.",
        "KI-Wächter für Verletzungen, Pressekonferenzen und Startelf-Chancen.",
      ) +
      `<div class="metrics">` +
      metric("Geprüfte Meldungen", fmt(u7), "Letzte 7 Tage") +
      metric("Kader-Eingriffe", fmt(f7), "Gesperrte Käufe & Warnungen") +
      metric("Trefferquote", quote, "Abgleich mit echten Startelf-Daten") +
      metric("Überwachte Spieler", fmt(watched), "Im eigenen Kader & Marktradar") +
      `</div>` +
      `<article class="panel"><div class="panel-head"><div>` +
      `<span class="eyebrow">Klartext-Protokoll</span>` +
      `<h2>Alle Jev-Urteile</h2></div>` +
      `${chip(feedRows.length + " Einträge")}</div>` +
      `${rowsHTML}</article>`
    );
  }
  function today() {
    return (
      title(
        "Heute.",
        gap === null
          ? ""
          : `${fmt(gap)} ${unit("Pkt.")} zum oberen Drittel · ${esc(scoreBasis)}`,
      ) +
      todayKPIsHTML() +
      controlHubHTML() +
      `<div class="grid-main"><div>${lineupPitchHTML()}${marketRadarHTML()}</div>` +
      `<div class="side-column">${panel("Relevante Nachrichten", "", newsHTML())}</div></div>`
    );
  }
  function managerPlayerFinance(p, profile) {
    if (!profile) return "";
"""
    '    const holding = arr(profile.holdings).find((h) => String(h.player_id) === String(p.pid'
    ')),\n'
    '      pairs = arr(profile.round_trips).filter((r) => String(r.player_id) === String(p.pid)'
    ');\n'
    r"""    const realized = pairs.length
      ? pairs.reduce((sum, r) => sum + (num(r.pnl_eur) ?? 0), 0)
      : null;
"""
    '    return `<div class="detail-block"><div class="panel-head"><div><h3>Bei ${esc(profile.n'
    'ame)}</h3><p>${holding ? "Aktuell im Kader" : "Historische Transfers dieses Managers"}</p>'
    '</div><button class="text-link" data-manager="${esc(profile.user_id)}">Zur Übersicht ↗</bu'
    'tton></div><div class="detail-metrics"><div><strong>${money(holding?.buy_amount)}</strong>'
    '<span>Belegter Einstand des gehaltenen Spielers</span></div><div><strong>${pct(holding?.bu'
    'y_premium_pct)}</strong><span>Aufschlag beim Kauf</span></div><div><strong class="${cls(ho'
    'lding?.unrealized_pnl_eur)}">${money(holding?.unrealized_pnl_eur)}</strong><span>Nicht rea'
    'lisiert</span></div><div><strong class="${cls(realized)}">${money(realized)}</strong><span'
    '>Realisiert aus ${pairs.length} vollständigen Paaren</span></div></div>${profileTrades(pro'
    'file, p.pid)}${note("Ein abgegebenes Gebot belegt keinen Kauf. Unbekannter Einstand bleibt'
    ' offen.")}</div>`;\n'
    r"""  }

  function lineChart(p) {
    const full = arr(playerData.get(String(p.pid))?.history)
        .filter((r) => num(r.value) !== null && Number.isFinite(Date.parse(r.date)))
        .sort((a, b) => Date.parse(a.date) - Date.parse(b.date)),
      last = full.length ? Date.parse(full.at(-1).date) : 0,
      values = full.filter(
        (r) =>
          historyPeriod === "all" ||
          last - Date.parse(r.date) <= Number(historyPeriod) * 86400000,
      );
    if (values.length < 2)
"""
    '      return empty("Noch kein belastbarer, datierter Marktwertverlauf für diesen Zeitraum.'
    '");\n'
    r"""    const W = 470,
      H = 168,
      L = 9,
      R = 9,
      T = 20,
      B = 26,
      lo = Math.min(...values.map((r) => r.value)),
      hi = Math.max(...values.map((r) => r.value)),
      span = hi - lo || 1,
      first = Date.parse(values[0].date),
      timeSpan = Date.parse(values.at(-1).date) - first || 1;
    const points = values.map((r) => ({
      x: L + ((Date.parse(r.date) - first) / timeSpan) * (W - L - R),
      y: H - B - ((r.value - lo) / span) * (H - T - B),
      r,
    }));
"""
    '    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Datiert'
    'er Marktwertverlauf"><line class="grid-line" x1="0" x2="${W}" y1="${H - B}" y2="${H - B}"/'
    '><polyline points="${points.map((p) => p.x + "," + p.y).join(" ")}" fill="none" stroke="va'
    'r(--chart)" stroke-width="2"/>${points.map((p) => `<circle cx="${p.x}" cy="${p.y}" r="3" f'
    'ill="var(--chart)" tabindex="0" data-tip="${esc(date(p.r.date) + "\\n" + money(p.r.value) +'
    ' "\\n" + String(p.r.source || ""))}"/>`).join("")}<text x="0" y="${H - 5}">${esc(date(value'
    's[0].date))}</text><text x="${W}" y="${H - 5}" text-anchor="end">${esc(date(values.at(-1).'
    'date))}</text></svg></div>${note(values.length + " datierte Werte. " + money(values[0].val'
    'ue) + " → " + money(values.at(-1).value) + ". Abstände entsprechen echten Tagen.")}`;\n'
    r"""  }
  function performanceChart(p) {
    const perf = arr(playerData.get(String(p.pid))?.performance),
      seasons = [...new Set(perf.map((r) => r.season))].sort(),
      latest = seasons.at(-1),
      items = perf
        .filter((r) => r.season === latest)
        .sort((a, b) => a.matchday - b.matchday)
        .slice(-10);
    if (!items.length) return empty("Noch keine einzelnen Spieltagsleistungen erfasst.");
    const W = 470,
      H = 145,
      L = 9,
      R = 9,
      T = 20,
      B = 25,
      hi = Math.max(...items.map((r) => r.points || 0), 1),
      lo = Math.min(0, ...items.map((r) => r.points || 0)),
      scale = (H - T - B) / (hi - lo),
      zero = H - B + lo * scale,
      width = (W - L - R) / items.length;
"""
    '    return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Tatsäch'
    'liche Punkte der letzten erfassten Spieltage"><line class="zero-line" x1="0" x2="${W}" y1='
    '"${zero}" y2="${zero}"/>${items\n'
    r"""      .map((r, i) => {
        const v = num(r.points);
        if (v === null) return "";
"""
    '        return `<rect x="${L + i * width + width * 0.22}" y="${Math.min(zero, zero - v * s'
    'cale)}" width="${width * 0.56}" height="${Math.max(1, Math.abs(v * scale))}" rx="2" fill="'
    '${v < 0 ? "var(--bad)" : "var(--chart)"}" tabindex="0" data-tip="${esc("Spieltag " + r.mat'
    'chday + "\\n" + fmt(v) + " Punkte\\n" + fmt(r.minutes) + " Minuten")}"/><text x="${L + i * w'
    'idth + width / 2}" y="${H - 5}" text-anchor="middle">${esc(r.matchday)}</text>`;\n'
    r"""      })
      .join(
        "",
"""
    '      )}</svg></div>${note("Saison " + esc(latest) + " · x-Achse: Spieltag. Tatsächliche P'
    'unkte, keine Prognose. Fehlende Minuten bleiben unbekannt.")}`;\n'
    r"""  }
  function openPlayer(pid, keepFocus = false, managerId = null) {
    const p = players.get(String(pid));
    if (!p) return;
    activePlayer = p;
    if (!keepFocus) {
      activeManagerProfile =
        managerProfiles.find(
          (m) => String(m.user_id) === String(managerId) || m.name === managerId,
        ) ||
        (view === "uebersicht"
          ? currentProfile()
          : managerProfiles.find((m) =>
              arr(m.holdings).some((h) => String(h.player_id) === String(pid)),
            )) ||
        managerProfiles.find((m) => m.me) ||
        null;
    }
    if (!keepFocus) {
      lastFocus = document.activeElement;
      historyPeriod = "all";
    }
    const pNews = relatedNews(p);
    document.getElementById("drawer-content").innerHTML =
      `<div class="drawer-heading">${portrait(p)}<div><h2 id="drawer-title">${esc(p.name)}` +
      `</h2><p>${esc(p.team || "Verein offen")} · ${esc(p.pos || "Position offen")}</p>` +
      `</div></div>${status(p)}${p.protected ? " " + chip("Verkaufsschutz aktiv") : ""}` +
      `${jevBadgeInline(p)}${observedRoleNote(p)}${agentDossierHTML(p)}` +
      `<div class="detail-metrics" style="margin-top:20px">` +
      `<div><strong>${money(p.mv)}</strong>` +
      `<span>Aktueller Marktwert (${money(p.mv24)} / 24h)</span></div>` +
      `<div><strong>${fmt(avgPointsOf(p))}</strong>` +
      `<span>Gemeldeter Punkteschnitt (${fmt(p.einsaetze)} Einsätze)</span></div>` +
      `<div><strong class="${effTone(ptsPerMio(avgPointsOf(p), p.mv), "avg")}">` +
      `${fmtRatio(ptsPerMio(avgPointsOf(p), p.mv))}</strong>` +
      `<span>Ø Punkte pro Mio. € Marktwert</span></div>` +
      `<div><strong class="${effTone(ptsPerMio(totalPointsOf(p), p.mv), "total")}">` +
      `${fmtRatio(ptsPerMio(totalPointsOf(p), p.mv))}</strong>` +
      `<span>Saisonpunkte pro Mio. € (${fmt(totalPointsOf(p))} Pkt.)</span></div></div>` +
      `${managerPlayerFinance(p, activeManagerProfile)}` +
      `<div class="detail-block"><div class="panel-head"><h3>Der Marktwert im Verlauf</h3>` +
      `<div class="segmented">${[
        ["7", "7 T"],
        ["30", "30 T"],
        ["all", "Alle"],
      ]
        .map(
          ([k, l]) =>
            `<button data-history="${k}" class="${historyPeriod === k ? "active" : ""}"` +
            ` aria-pressed="${historyPeriod === k}">${l}</button>`,
        )
        .join("")}</div></div><div id="history-chart">${lineChart(p)}</div></div>` +
      `<div class="detail-block"><h3>Punkte und Einsatzminuten</h3>` +
      `${performanceChart(p)}</div>` +
      (arr(p.spiele).length
        ? `<div class="detail-block"><h3>Spiele im gelieferten Zeitfenster</h3>` +
          `<div class="fixtures">${arr(p.spiele)
            .slice(-4)
            .map(
              (f) =>
                `<div class="fixture"><strong>${esc(f.home)} – ${esc(f.away)}</strong>` +
                `<small>${esc(f.date)}${f.score ? " · " + esc(f.score) : ""}</small></div>`,
            )
            .join("")}</div></div>`
        : "") +
      (pNews.length
        ? `<div class="detail-block"><h3>Nachrichten zu ${esc(p.name)}</h3>` +
          `${newsHTML(pNews)}</div>`
        : "");
    if (!document.getElementById("drawer").open) document.getElementById("drawer").showModal();
    if (!keepFocus) document.getElementById("drawer-close").focus();
  }
  function rememberManager(id) {
"""
    '    const m = managerProfiles.find((m) => String(m.user_id) === String(id) || m.name === i'
    'd);\n'
    r"""    if (!m) return false;
    overviewManager = String(m.user_id);
    try {
      localStorage.setItem("kickbase.overview.manager", overviewManager);
    } catch {}
    return true;
  }
  function openManager(id) {
    if (!rememberManager(id)) return;
    if (document.getElementById("drawer").open) document.getElementById("drawer").close();
    query = "";
    position = "";
    tradeFilter = "all";
    switchView("uebersicht");
  }
  function updateOverviewRows() {
    const roster = managerRoster(),
      q = normSearch(query.trim()),
      filtered = roster.filter(
        (p) =>
          (!q ||
            normSearch(`${p.search_name || ""} ${p.name || ""} ${p.team || ""}`).includes(q)) &&
          (!position || String(p.position) === position),
      );
    document.getElementById("squad-table").innerHTML = overviewSquadSection(filtered, roster);
  }
  let rulesState = {
    mode: "full_autopilot",
    allow_bids: true,
    max_bid_million: 50.0,
    max_overpay_pct: 8.0,
    allow_sells: "swap_allowed",
    allow_mw_trading: true,
    trade_budget_cap_million: 50.0,
    friday_buffer_haircut_pct: 0.0,
    allow_lineup: true,
    allow_friday_rescue: true,
    veto_minutes: 30,
    protected_players: [],
    ...(D.rules && typeof D.rules === "object" ? D.rules : {}),
  };
  function loadRulesFromBackend() {
    try {
      chainVal(
        apiCall("/api/rules", "GET"),
        (res) => {
          const next = res?.rules || (res?.mode ? res : null);
          if (next && typeof next === "object") {
            rulesState = { ...rulesState, ...next };
            if (view === "regeln") render();
          }
        },
        () => {},
      );
    } catch (_) {}
  }
  function regelnView() {
    const r = rulesState || {};
    const protSet = new Set(
      (r.protected_players || []).map((x) => String(x).trim().toLowerCase()),
    );
    const squadChips = (D.kader || [])
      .map((p) => {
        const nm = String(p.name || "");
        const locked =
          protSet.has(nm.toLowerCase()) ||
          protSet.has(String(p.pid || "").toLowerCase());
        const cls = locked ? "button btn-sm" : "button secondary btn-sm";
        const pfx = locked ? "&#128274; " : "";
        return (
          `<button type="button" class="${cls}" ` +
          `data-toggle-protect="${esc(nm)}" style="margin:3px">` +
          `${pfx}${esc(nm)}</button>`
        );
      })
      .join("");
    const modeLabel =
      r.mode === "dry_run"
        ? "Nur Berater (Dry-Run)"
        : r.mode === "veto_copilot"
          ? "Co-Pilot mit Veto-Countdown"
          : "Voll-Autopilot";
    const rowStyle =
      "display:flex;justify-content:space-between;align-items:center;gap:12px";
    return [
      `<div class="page-title"><div>`,
      `<span class="eyebrow">KI-REGELWERK &amp; ONBOARDING</span>`,
      `<h1>Regelwerk &amp; Autonomie.</h1>`,
      `<p>Aktives Profil: <strong>${esc(modeLabel)}</strong> · `,
      `Synchron zwischen Web-App und KI-Chat (<code>data/agent_rules.json</code>).</p>`,
      `</div><span class="tag good">${esc(modeLabel)}</span></div>`,
      `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));`,
      `gap:16px;margin-bottom:24px">`,
      `<div class="card" style="padding:18px"><span class="eyebrow">PROFIL 1</span>`,
      `<h3>Nur Berater (Dry-Run)</h3>`,
      `<p style="color:var(--muted);font-size:13px">Rechnet Kader, 4-Säulen-Prognose, `,
      `Jev-News und Markt komplett durch, gibt aber niemals echte Gebote ab.</p>`,
      `<button type="button" class="button ${r.mode === "dry_run" ? "" : "secondary"}" `,
      `data-rule-preset="dry_run">Profil aktivieren</button></div>`,
      `<div class="card" style="padding:18px"><span class="eyebrow">PROFIL 2</span>`,
      `<h3>Co-Pilot mit Veto-Countdown</h3>`,
      `<p style="color:var(--muted);font-size:13px">Plant Transfers und Marktwert-Trades `,
      `mit Countdown im Cockpit. Du behältst jederzeit das letzte Wort.</p>`,
      `<button type="button" class="button ${r.mode === "veto_copilot" ? "" : "secondary"}" `,
      `data-rule-preset="veto_copilot">Profil aktivieren</button></div>`,
      `<div class="card" style="padding:18px"><span class="eyebrow">PROFIL 3</span>`,
      `<h3>Voll-Autopilot</h3>`,
      `<p style="color:var(--muted);font-size:13px">Führt Stammelf-Upgrades, `,
      `Unter-der-Woche-Trading, Startelf und Freitags-Dispo selbstständig aus.</p>`,
      `<button type="button" class="button ${r.mode === "full_autopilot" ? "" : "secondary"}" `,
      `data-rule-preset="full_autopilot">Profil aktivieren</button></div></div>`,
      `<div class="card" style="padding:22px;display:grid;gap:16px">`,
      `<h3>Fein-Einstellungen (Was darf der Agent und was ist tabu?)</h3>`,
      `<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(280px,1fr));`,
      `gap:16px">`,
      `<label style="${rowStyle}"><span><strong>Gebote abgeben erlaubt</strong><br>`,
      `<small style="color:var(--muted)">Auf Transfermarkt-Spieler bieten</small></span>`,
      `<input type="checkbox" id="rule-allow-bids" ${r.allow_bids ? "checked" : ""}></label>`,
      `<label style="${rowStyle}"><span><strong>Max. Einzelgebot (Mio. €)</strong><br>`,
      `<small style="color:var(--muted)">Obergrenze ohne Rückfrage</small></span>`,
      `<input type="number" id="rule-max-bid" step="0.5" min="0.5" max="95" `,
      `value="${Number(r.max_bid_million ?? 50)}" style="width:95px"></label>`,
      `<label style="${rowStyle}"><span><strong>Max. Overpay über Marktwert (%)</strong><br>`,
      `<small style="color:var(--muted)">Maximaler Aufschlag bei Auktionen</small></span>`,
      `<input type="number" id="rule-max-overpay" step="0.5" min="0" max="25" `,
      `value="${Number(r.max_overpay_pct ?? 8)}" style="width:95px"></label>`,
      `<label style="${rowStyle}"><span><strong>Spieler-Verkäufe</strong><br>`,
      `<small style="color:var(--muted)">Welche Spieler darf die KI verkaufen?</small></span>`,
      `<select id="rule-allow-sells">`,
      `<option value="none" ${r.allow_sells === "none" ? "selected" : ""}>Nie verkaufen</option>`,
      `<option value="bench_only" ${r.allow_sells === "bench_only" ? "selected" : ""}>`,
      `Nur Bank &amp; Trading</option>`,
      `<option value="swap_allowed" ${r.allow_sells === "swap_allowed" ? "selected" : ""}>`,
      `Auch Stammspieler bei Upgrade</option></select></label>`,
      `<label style="${rowStyle}"><span><strong>Unter-der-Woche Marktwert-Trading</strong><br>`,
      `<small style="color:var(--muted)">Steiger auf Dispo kaufen &amp; verkaufen</small></span>`,
      `<input type="checkbox" id="rule-mw-trading" ${r.allow_mw_trading ? "checked" : ""}></label>`,
      `<label style="${rowStyle}"><span><strong>Max. Trading-Dispo (Mio. €)</strong><br>`,
      `<small style="color:var(--muted)">Kapitalrahmen für Kurzfrist-Steiger</small></span>`,
      `<input type="number" id="rule-trade-cap" step="1" min="0" max="80" `,
      `value="${Number(r.trade_budget_cap_million ?? 50)}" style="width:95px"></label>`,
      `<label style="${rowStyle}"><span><strong>Freitags-Sicherheits-Haircut (%)</strong><br>`,
      `<small style="color:var(--muted)">Puffer pro 22:00-Update bis Freitag</small></span>`,
      `<input type="number" id="rule-haircut" step="0.5" min="0" max="15" `,
      `value="${Number(r.friday_buffer_haircut_pct ?? 3.5)}" style="width:95px"></label>`,
      `<label style="${rowStyle}"><span><strong>Startelf automatisch aufstellen</strong><br>`,
      `<small style="color:var(--muted)">Beste 11 vor Spieltagsbeginn setzen</small></span>`,
      `<input type="checkbox" id="rule-lineup" ${r.allow_lineup ? "checked" : ""}></label>`,
      `<label style="${rowStyle}"><span><strong>Freitagabend Konto-Rettung (≥ 0 €)</strong><br>`,
      `<small style="color:var(--muted)">Vor Anpfiff Dispo garantiert ausgleichen</small></span>`,
      `<input type="checkbox" id="rule-friday-rescue" `,
      `${r.allow_friday_rescue ? "checked" : ""}></label>`,
      `<label style="${rowStyle}"><span><strong>Veto-Countdown (Minuten)</strong><br>`,
      `<small style="color:var(--muted)">Reaktionszeit vor Ausführung</small></span>`,
      `<input type="number" id="rule-veto-minutes" step="5" min="5" max="240" `,
      `value="${Number(r.veto_minutes ?? 30)}" style="width:95px"></label></div>`,
      `<div style="border-top:1px solid var(--line);padding-top:14px">`,
      `<strong>Unantastbare Lieblingsspieler (Schutzliste · Klick zum Sperren)</strong>`,
      `<div style="margin:8px 0">${squadChips || "<small>Kader wird geladen…</small>"}</div>`,
      `<input type="text" id="rule-protected" `,
      `value="${esc((r.protected_players || []).join(", "))}" `,
      `placeholder="Kommagetrennte Spielernamen" style="width:100%"></div>`,
      `<div style="${rowStyle};border-top:1px solid var(--line);padding-top:14px">`,
      `<small style="color:var(--muted)">Synchron mit KI-Onboarding im Chat: `,
      `<code>uv --directory api run kba onboarding</code></small>`,
      `<button type="button" class="button" id="rules-save-btn">Regelwerk speichern</button>`,
      `</div></div>`,
    ].join("");
  }
  function render() {
    document.getElementById("content").innerHTML =
      view === "uebersicht"
        ? overview()
        : view === "liga"
          ? league()
          : view === "spieler"
            ? spielerView()
            : view === "jev"
              ? jevView()
              : view === "regeln"
                ? regelnView()
                : today();
    document.getElementById("breadcrumb").innerHTML =
      "Meine Saison <span>/</span> " +
      {
        heute: "Heute",
        spieler: "Alle Spieler",
        uebersicht: "Übersicht",
        liga: "Liga",
        jev: "Jev-Analyse",
        regeln: "Regelwerk",
      }[view];
    document.querySelectorAll("[data-view]").forEach((b) => {
      b.classList.toggle("active", b.dataset.view === view);
      if (b.dataset.view === view) b.setAttribute("aria-current", "page");
      else b.removeAttribute("aria-current");
    });
    updateCalc();
  }
  function switchView(next) {
    if (document.getElementById("drawer")?.open) document.getElementById("drawer").close();
    view = ["heute", "spieler", "uebersicht", "liga", "jev", "regeln"].includes(next)
      ? next
      : next === "mannschaft"
        ? "uebersicht"
        : "heute";
    history.replaceState(
      null,
      "",
"""
    '      "#" + view + (overviewManager ? "?manager=" + encodeURIComponent(overviewManager) : '
    '""),\n'
    r"""    );
    render();
    if (view === "regeln") loadRulesFromBackend();
    window.scrollTo({ top: 0, behavior: "instant" });
  }
  function showBackendNotice(msg, tone = "good") {
    lastBackendMsg = msg || "";
    const toastEl = document.getElementById("kba-toast");
    if (toastEl) {
      toastEl.textContent = lastBackendMsg;
      toastEl.className = "toast " + tone;
      toastEl.hidden = !lastBackendMsg;
    }
    const statusEl = document.getElementById("live-status");
    if (statusEl && lastBackendMsg) {
      statusEl.className = "snapshot is-live";
      statusEl.innerHTML = `<i></i>${esc(lastBackendMsg)}`;
    }
  }
  function chainVal(val, onOk, onErr) {
    if (val && typeof val.then === "function") return val.then(onOk, onErr);
    try {
      return onOk(val);
    } catch (err) {
      if (onErr) return onErr(err);
      throw err;
    }
  }
  function apiCall(path, method = "GET", body = undefined) {
    const fn = window.__kbaFetch || (typeof fetch === "function" ? fetch : null);
    if (!fn) throw new Error("Backend-API im Offline-Schnappschuss nicht erreichbar.");
    const headers = { "X-Requested-With": "kba" };
    if (body !== undefined) headers["Content-Type"] = "application/json";
    const opts = {
      method,
      headers,
      ...(body !== undefined ? { body: JSON.stringify(body) } : {}),
    };
    return chainVal(fn(path, opts), (res) => {
      const parsed =
        res && typeof res.json === "function" ? res.json() : res?.data ?? res ?? {};
      return chainVal(parsed, (data) => {
        if (res && res.ok === false) {
          throw new Error(data?.detail || "HTTP " + (res.status || 500));
        }
        return data || {};
      });
    });
  }
  function refreshBackendViews() {
    if (view === "heute") {
      render();
    } else {
      const hub = document.getElementById("control-hub");
      if (hub) hub.outerHTML = controlHubHTML();
    }
    if (document.getElementById("drawer")?.open && activePlayer) {
      openPlayer(activePlayer.pid, true, activeManagerProfile?.user_id || null);
    }
  }
  function applyUpdatedIntent(id, updated, fallbackPatch = {}) {
    const idx = liveIntents.findIndex((x) => String(x.id) === String(id));
    if (idx >= 0) {
      liveIntents[idx] = { ...liveIntents[idx], ...fallbackPatch, ...(updated || {}) };
    } else if (updated && updated.id !== undefined) {
      liveIntents.unshift(updated);
    }
  }
  function runIntentAction(act, rawId) {
    const id = String(rawId || "").trim();
    if (!id) return;
    if (act === "toggle-amount") {
      openAmountIntentId = String(openAmountIntentId) === id ? null : id;
      refreshBackendViews();
      return;
    }
    const cur = liveIntents.find((x) => String(x.id) === id) || { id };
    const pName = cur.player_name || players.get(String(cur.player_id || ""))?.name || "#" + id;
    if (act === "exec") {
      try {
        chainVal(
          apiCall(`/api/intents/${encodeURIComponent(id)}/execute-now`, "POST", {}),
          (data) => {
            applyUpdatedIntent(id, data?.intent, {
              status: "planned",
              seconds_left: 0,
              execute_at: new Date().toISOString(),
            });
            showBackendNotice(
              data?.note || `Sofort-Ausführung für ${pName} im Backend vorgemerkt.`,
              "good",
            );
            refreshBackendViews();
          },
          (err) => {
            showBackendNotice("Fehler: " + (err?.message || err), "bad");
            refreshBackendViews();
          },
        );
      } catch (err) {
        showBackendNotice("Fehler: " + (err?.message || err), "bad");
        refreshBackendViews();
      }
      return;
    }
    if (act === "plus10" || act === "plus20") {
      const pctStep = act === "plus10" ? 10 : 20,
        baseAmt = num(cur.amount) ?? 0,
        nextAmt = Math.round(baseAmt * (1 + pctStep / 100));
      try {
        chainVal(
          apiCall(`/api/intents/${encodeURIComponent(id)}/adjust`, "POST", { pct: pctStep }),
          (data) => {
            applyUpdatedIntent(id, data?.intent, {
              adjusted_from: cur.adjusted_from ?? cur.amount,
              amount: data?.intent?.amount ?? nextAmt,
            });
            const finalAmt =
              num(liveIntents.find((x) => String(x.id) === id)?.amount) ?? nextAmt;
            showBackendNotice(
              `Preis für ${pName} um +${pctStep} % auf ${money(finalAmt)} erhöht.`,
              "good",
            );
            refreshBackendViews();
          },
          (err) => {
            showBackendNotice("Fehler: " + (err?.message || err), "bad");
            refreshBackendViews();
          },
        );
      } catch (err) {
        showBackendNotice("Fehler: " + (err?.message || err), "bad");
        refreshBackendViews();
      }
      return;
    }
    if (act === "amount") {
      const inp = document.getElementById("intent-amount-input-" + id),
        rawVal = Number(inp?.value || 0);
      if (!Number.isFinite(rawVal) || rawVal <= 0) {
        showBackendNotice("Bitte einen gültigen Euro-Betrag (> 0) eingeben.", "bad");
        refreshBackendViews();
        return;
      }
      const euroVal = Math.round(rawVal);
      try {
        chainVal(
          apiCall(`/api/intents/${encodeURIComponent(id)}/adjust`, "POST", { amount: euroVal }),
          (data) => {
            openAmountIntentId = null;
            applyUpdatedIntent(id, data?.intent, {
              adjusted_from: cur.adjusted_from ?? cur.amount,
              amount: data?.intent?.amount ?? euroVal,
            });
            showBackendNotice(
              `Wunschpreis für ${pName} auf ${money(euroVal)} im Backend gespeichert.`,
              "good",
            );
            refreshBackendViews();
          },
          (err) => {
            showBackendNotice("Fehler: " + (err?.message || err), "bad");
            refreshBackendViews();
          },
        );
      } catch (err) {
        showBackendNotice("Fehler: " + (err?.message || err), "bad");
        refreshBackendViews();
      }
      return;
    }
    if (act === "veto") {
      const noteEl = document.getElementById("intent-veto-note-" + id),
        noteTxt = String(noteEl?.value || "").trim() || "Per Web-App gestoppt";
      try {
        chainVal(
          apiCall(`/api/intents/${encodeURIComponent(id)}/veto`, "POST", { note: noteTxt }),
          (data) => {
            openAmountIntentId = null;
            applyUpdatedIntent(id, data?.intent, {
              status: "vetoed",
              emil_note: noteTxt,
            });
            showBackendNotice(
              data?.note || `Veto gespeichert — ${pName} wurde gestoppt.`,
              "good",
            );
            refreshBackendViews();
          },
          (err) => {
            showBackendNotice("Fehler: " + (err?.message || err), "bad");
            refreshBackendViews();
          },
        );
      } catch (err) {
        showBackendNotice("Fehler: " + (err?.message || err), "bad");
        refreshBackendViews();
      }
    }
  }
  function syncBackend() {
    try {
      chainVal(
        apiCall("/api/intents?status=all&limit=40", "GET"),
        (data) => {
          if (Array.isArray(data?.intents)) liveIntents = data.intents.map((i) => ({ ...i }));
          return chainVal(
            apiCall("/api/protocol?limit=20", "GET"),
            (pData) => {
              if (Array.isArray(pData?.entries)) {
                liveProtocol = pData.entries.map((e) => ({ ...e }));
              }
              showBackendNotice(
                `Backend synchronisiert (${liveIntents.length} Vorhaben geladen).`,
                "good",
              );
              refreshBackendViews();
            },
            () => {
              showBackendNotice(
                `Backend synchronisiert (${liveIntents.length} Vorhaben geladen).`,
                "good",
              );
              refreshBackendViews();
            },
          );
        },
        (err) => {
          showBackendNotice("Sync-Hinweis: " + (err?.message || err), "bad");
          refreshBackendViews();
        },
      );
    } catch (err) {
      showBackendNotice("Sync-Hinweis: " + (err?.message || err), "bad");
      refreshBackendViews();
    }
  }
  function triggerRefresh() {
    try {
      chainVal(
        apiCall("/api/refresh", "POST", {}),
        (data) => {
          showBackendNotice(
            data?.note || "Cockpit-Neuberechnung im Backend gestartet.",
            "good",
          );
          refreshBackendViews();
        },
        (err) => {
          showBackendNotice("Refresh-Hinweis: " + (err?.message || err), "bad");
          refreshBackendViews();
        },
      );
    } catch (err) {
      showBackendNotice("Refresh-Hinweis: " + (err?.message || err), "bad");
      refreshBackendViews();
    }
  }
  document.addEventListener("click", (e) => {
    const player = e.target.closest("[data-player]");
    if (player) {
      openPlayer(player.dataset.player, false, player.dataset.playerManager || null);
      return;
    }
    const manager = e.target.closest("[data-manager]");
    if (manager) {
      openManager(manager.dataset.manager);
      return;
    }
    const b = e.target.closest("button,a");
    if (!b) return;
    if (b.dataset.intentAct) {
      runIntentAction(b.dataset.intentAct, b.dataset.intentId);
      return;
    }
    if (b.dataset.backendAct === "sync" || b.id === "kba-sync") {
      syncBackend();
      return;
    }
    if (b.dataset.backendAct === "refresh" || b.id === "kba-refresh") {
      triggerRefresh();
      return;
    }
    if (b.dataset.ownManager !== undefined || b.dataset.ownOverview !== undefined) {
      openManager(managerProfiles.find((m) => m.me)?.user_id || my.user_id);
      return;
    }
    if (b.dataset.pointsMode) {
      pointsMode = b.dataset.pointsMode;
"""
    '      document.getElementById("points-history").innerHTML = pointHistory(currentProfile())'
    ';\n'
    r"""      document
        .querySelectorAll("[data-points-mode]")
"""
    '        .forEach((x) => x.classList.toggle("active", x.dataset.pointsMode === pointsMode))'
    ';\n'
    r"""    }
    if (b.dataset.financeMode) {
      financeMode = b.dataset.financeMode;
      document.getElementById("financial-history").innerHTML =
        financialHistory(currentProfile());
      document
        .querySelectorAll("[data-finance-mode]")
"""
    '        .forEach((x) => x.classList.toggle("active", x.dataset.financeMode === financeMode'
    '));\n'
    r"""    }
    if (b.dataset.tradeFilter) {
      tradeFilter = b.dataset.tradeFilter;
"""
    '      document.getElementById("profile-transfers").innerHTML = profileTrades(currentProfil'
    'e());\n'
    r"""      document
        .querySelectorAll("[data-trade-filter]")
"""
    '        .forEach((x) => x.classList.toggle("active", x.dataset.tradeFilter === tradeFilter'
    '));\n'
    r"""    }
"""
    '    if (b.dataset.view || b.dataset.switch) switchView(b.dataset.view || b.dataset.switch)'
    ';\n'
    r"""    if (b.dataset.jump)
      document.getElementById(b.dataset.jump)?.scrollIntoView({
        behavior: matchMedia("(prefers-reduced-motion: reduce)").matches
          ? "instant"
          : "smooth",
        block: "start",
      });
    if (b.id === "package-add") {
      const id = document.getElementById("package-player").value,
        p = players.get(id);
      if (p && num(p.mv) !== null && !packageItems.some((x) => String(x.pid) === id)) {
        packageItems.push({ ...p, amount: p.mv });
        const y = scrollY;
        render();
        window.scrollTo(0, y);
      }
    }
    if (b.dataset.packageRemove !== undefined) {
      packageItems.splice(Number(b.dataset.packageRemove), 1);
      const y = scrollY;
      render();
      window.scrollTo(0, y);
    }
    if (b.dataset.period) {
      period = b.dataset.period;
      document.querySelectorAll("[data-period]").forEach((x) => {
        x.classList.toggle("active", x.dataset.period === period);
        x.setAttribute("aria-pressed", String(x.dataset.period === period));
      });
      document.getElementById("premium-chart").innerHTML = premiumChart();
    }
    if (b.dataset.series) {
      if (b.dataset.series === "own") showOwn = !showOwn;
      else showOthers = !showOthers;
      document.getElementById("premium-chart").innerHTML = premiumChart();
    }
    if (b.dataset.history) {
      historyPeriod = b.dataset.history;
      document.getElementById("history-chart").innerHTML = lineChart(activePlayer);
      document.querySelectorAll("[data-history]").forEach((x) => {
        x.classList.toggle("active", x.dataset.history === historyPeriod);
        x.setAttribute("aria-pressed", String(x.dataset.history === historyPeriod));
      });
    }
    if (b.dataset.effPool) {
      effPool = b.dataset.effPool;
      effShowAll = false;
      updateEfficiencyView();
    }
    if (b.dataset.effSort) {
      effSort = b.dataset.effSort;
      updateEfficiencyView();
    }
    if (b.id === "eff-toggle-all") {
      effShowAll = !effShowAll;
      updateEfficiencyView();
    }
    if (b.dataset.manager) openManager(b.dataset.manager);
    if (b.id === "drawer-close") document.getElementById("drawer").close();
    if (b.dataset.rulePreset) applyRulePreset(b.dataset.rulePreset);
    if (b.dataset.toggleProtect) toggleProtectedPlayer(b.dataset.toggleProtect);
    if (b.id === "rules-save-btn") saveRulesFromView();
  });
  document.addEventListener("input", (e) => {
    if (e.target.id === "player-search") {
      query = e.target.value;
      updateOverviewRows();
    }
    if (e.target.id === "eff-search") {
      effQuery = e.target.value;
      updateEfficiencyView();
    }
    if (e.target.id === "reserve") {
      reserve = e.target.value;
      updateCalc();
    }
    if (e.target.dataset.packageAmount !== undefined) {
      packageItems[Number(e.target.dataset.packageAmount)].amount =
        e.target.value === "" ? null : Number(e.target.value);
      updateCalc();
    }
  });
  document.addEventListener("change", (e) => {
    if (e.target.id === "overview-manager") {
      openManager(e.target.value);
      return;
    }
    if (e.target.id === "position-filter") {
      position = e.target.value;
      updateOverviewRows();
    }
    if (e.target.id === "eff-pos") {
      effPos = e.target.value;
      updateEfficiencyView();
    }
    if (e.target.id === "manager-filter") {
      selectedManager = e.target.value;
      document.getElementById("premium-chart").innerHTML = premiumChart();
    }
    if (e.target.id === "floor-filter") {
      includeFloor = e.target.checked;
      document.getElementById("premium-chart").innerHTML = premiumChart();
    }
  });
  const tip = document.getElementById("tooltip");
  function showTip(target, x, y) {
    tip.textContent = target.dataset.tip;
    tip.hidden = false;
"""
    '    tip.style.left = Math.max(8, Math.min(x + 14, innerWidth - tip.offsetWidth - 12)) + "p'
    'x";\n'
    '    tip.style.top = Math.max(8, Math.min(y + 12, innerHeight - tip.offsetHeight - 12)) + "'
    'px";\n'
    r"""  }
  document.addEventListener("pointerover", (e) => {
    const t = e.target.closest("[data-tip]");
    if (t) showTip(t, e.clientX, e.clientY);
    else tip.hidden = true;
  });
  document.addEventListener("pointermove", (e) => {
    if (!tip.hidden) {
      const t = e.target.closest("[data-tip]");
      if (t) showTip(t, e.clientX, e.clientY);
    }
  });
  document.addEventListener("focusin", (e) => {
    if (e.target.dataset.tip) {
      const r = e.target.getBoundingClientRect();
      showTip(e.target, r.x, r.y);
    } else tip.hidden = true;
  });
  document.addEventListener("keydown", (e) => {
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("circle[data-player]")) {
      e.preventDefault();
      openPlayer(e.target.dataset.player, false, e.target.dataset.playerManager || null);
    }
    if ((e.key === "Enter" || e.key === " ") && e.target.matches("tr[data-manager]")) {
      e.preventDefault();
      openManager(e.target.dataset.manager);
    }
    if (e.key === "Escape") tip.hidden = true;
  });
  const drawer = document.getElementById("drawer");
  drawer.addEventListener("close", () => {
    tip.hidden = true;
    lastFocus?.focus();
  });
  if (typeof HTMLDialogElement === "undefined" || !("closedBy" in HTMLDialogElement.prototype)) {
    drawer.addEventListener("click", (event) => {
      if (event.target !== drawer) return;
      const rect = drawer.getBoundingClientRect();
      const isDialogContent =
        rect.top <= event.clientY &&
        event.clientY <= rect.top + rect.height &&
        rect.left <= event.clientX &&
        event.clientX <= rect.left + rect.width;
      if (isDialogContent) return;
      drawer.close();
    });
  }
  function applyRulePreset(preset) {
    try {
      chainVal(
        apiCall("/api/rules", "PUT", { preset }),
        (res) => {
          if (res?.rules) rulesState = res.rules;
          showBackendNotice("Profil aktiviert: " + preset, "good");
          render();
        },
        () => {},
      );
    } catch (_) {}
  }
  function toggleProtectedPlayer(name) {
    const list = Array.isArray(rulesState.protected_players)
      ? [...rulesState.protected_players]
      : [];
    const idx = list.findIndex((x) => String(x).toLowerCase() === String(name).toLowerCase());
    if (idx >= 0) list.splice(idx, 1);
    else list.push(name);
    rulesState.protected_players = list;
    render();
  }
  function saveRulesFromView() {
    const payload = {
      allow_bids: !!document.getElementById("rule-allow-bids")?.checked,
      max_bid_million: Number(document.getElementById("rule-max-bid")?.value || 50),
      max_overpay_pct: Number(document.getElementById("rule-max-overpay")?.value || 8),
      allow_sells: document.getElementById("rule-allow-sells")?.value || "swap_allowed",
      allow_mw_trading: !!document.getElementById("rule-mw-trading")?.checked,
      trade_budget_cap_million: Number(document.getElementById("rule-trade-cap")?.value || 50),
      friday_buffer_haircut_pct: Number(document.getElementById("rule-haircut")?.value || 3.5),
      allow_lineup: !!document.getElementById("rule-lineup")?.checked,
      allow_friday_rescue: !!document.getElementById("rule-friday-rescue")?.checked,
      veto_minutes: Number(document.getElementById("rule-veto-minutes")?.value || 30),
      protected_players: document.getElementById("rule-protected")?.value || "",
    };
    try {
      chainVal(
        apiCall("/api/rules", "PUT", payload),
        (res) => {
          if (res?.rules) rulesState = res.rules;
          showBackendNotice("KI-Regelwerk gespeichert.", "good");
          render();
        },
        () => {},
      );
    } catch (_) {}
  }
  function readRoute() {
    const [slug, params] = location.hash.slice(1).split("?"),
      id = new URLSearchParams(params || "").get("manager");
    if (id) rememberManager(id);
    view = ["heute", "spieler", "uebersicht", "liga", "jev", "regeln"].includes(slug)
      ? slug
      : slug === "mannschaft"
        ? "uebersicht"
        : "heute";
  }
  window.addEventListener("hashchange", () => {
    readRoute();
    render();
  });
  readRoute();
  document.getElementById("manager-count").textContent = rank.length
    ? rank.length + " Manager"
    : "";
  document.getElementById("manager-name").textContent = meName;
  render();
  if (typeof fetch === "function") loadRulesFromBackend();
})();
"""
)
