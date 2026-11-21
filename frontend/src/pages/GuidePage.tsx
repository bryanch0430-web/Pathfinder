/*
 * GuidePage — how to use Pathfinder, told through one real, tested run.
 *
 * Every screenshot in /public/guide comes from `frontend/scripts/capture_guide.py`,
 * which drives the live app. When a user-facing flow changes, update the copy
 * here and re-run that script in the same change. The trips are planned by a live
 * model, so after a re-shoot read every shot again: place names and wording change.
 */
import { useEffect, useState, type ReactNode } from 'react';
import { AppBar } from '../components/common/AppBar';
import '../styles/guide.css';

interface GuidePageProps {
  onNavigateHome?: () => void;
  onNavigateExplore?: () => void;
  onNavigateProfile?: () => void;
  onNavigateTrips?: () => void;
  onStartPlanning?: () => void;
}

const LAST_UPDATED = 'September 30, 2026';

const SECTIONS = [
  { id: 'overview', label: 'How Pathfinder works' },
  { id: 'pages', label: 'Finding your way around' },
  { id: 'start', label: 'Start a trip' },
  { id: 'one-shot', label: 'The full itinerary' },
  { id: 'changing', label: 'Changing the plan' },
  { id: 'studio', label: 'The Trip Studio' },
  { id: 'sketch', label: 'The sketchbook' },
  { id: 'map', label: 'Map & 3D view' },
  { id: 'export', label: 'Export & PDF' },
  { id: 'budget', label: 'Budget planner' },
  { id: 'agent', label: 'Build with your guide' },
  { id: 'agent-dock', label: 'The day dock' },
  { id: 'agent-rules', label: 'Rules & heads-ups' },
  { id: 'connected', label: 'How it all connects' },
  { id: 'phone', label: 'On your phone' },
  { id: 'phrases', label: 'Things you can say' },
  { id: 'faq', label: 'Questions & fixes' },
  { id: 'whats-new', label: "What's new" },
];

function Shot({ src, alt, children, portrait = false }: { src: string; alt: string; children: ReactNode; portrait?: boolean }) {
  return (
    <figure className={`tv-howto__shot${portrait ? ' tv-howto__shot--portrait' : ''}`}>
      <a href={`/guide/${src}.png`} target="_blank" rel="noreferrer" aria-label={`Open full-size screenshot: ${alt}`}>
        <img src={`/guide/${src}.png`} alt={alt} loading="lazy" width={portrait ? 390 : 1440} height={portrait ? 844 : 900} />
      </a>
      <figcaption>{children}</figcaption>
    </figure>
  );
}

function Steps({ children }: { children: ReactNode }) {
  return <ol className="tv-howto__steps">{children}</ol>;
}

/** Scroll to a section without a hash navigation: App treats popstate as a route change. */
function goToSection(id: string) {
  document.getElementById(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  window.history.replaceState(null, '', `#${id}`);
}

export function GuidePage(props: GuidePageProps) {
  const [active, setActive] = useState(SECTIONS[0].id);

  // Deep links such as /guide#budget land on their section once the page has rendered.
  useEffect(() => {
    const id = window.location.hash.slice(1);
    if (!id) return;
    // The route transition resets scroll after mount, so jump once it has settled.
    const timer = window.setTimeout(() => document.getElementById(id)?.scrollIntoView({ block: 'start' }), 400);
    return () => window.clearTimeout(timer);
  }, []);

  // Highlight the section being read in the contents list.
  useEffect(() => {
    const observer = new IntersectionObserver((entries) => {
      const visible = entries.filter((entry) => entry.isIntersecting)
        .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top)[0];
      if (visible) setActive(visible.target.id);
    }, { rootMargin: '-15% 0px -70% 0px' });
    SECTIONS.forEach(({ id }) => { const el = document.getElementById(id); if (el) observer.observe(el); });
    return () => observer.disconnect();
  }, []);

  return (
    <div className="tv2 tv2-app tv-howto">
      <AppBar current="guide" {...props} />
      <main className="tv-container tv-howto__main">
        <header className="tv-howto__intro">
          <span className="tv-eyebrow">Guide</span>
          <h1 className="tv-display tv-page__title">How to use <em>Pathfinder.</em></h1>
          <p className="tv-lead">
            Everything Pathfinder does and how to move around it, shown with two real trips planned while writing this
            guide: a 3-day Kyoto trip written in one go, and a 3-day Tokyo trip built day by day with a guide. Every
            screenshot is the live app.
          </p>
          <p className="tv-meta">Last updated {LAST_UPDATED}</p>
        </header>

        <div className="tv-howto__layout">
          <nav className="tv-howto__toc" aria-label="Guide contents">
            <span className="tv-label">CONTENTS</span>
            <ol>
              {SECTIONS.map(({ id, label }) => (
                <li key={id}>
                  <a href={`#${id}`} onClick={(event) => { event.preventDefault(); goToSection(id); }}
                    aria-current={active === id ? 'location' : undefined}
                    className={active === id ? 'is-active' : ''}>{label}</a>
                </li>
              ))}
            </ol>
            {props.onStartPlanning && (
              <button type="button" className="tv-btn tv-btn--primary tv-btn--sm" onClick={props.onStartPlanning}>
                Open the planner
              </button>
            )}
          </nav>

          <article className="tv-howto__body">
            <section id="overview">
              <h2>How Pathfinder works</h2>
              <p>
                You plan in a <strong>chat</strong> with a guide. The plan lives in the <strong>Trip Studio</strong>: one
                page with the day plan, a hand-drawn sketchbook, a map, a 3D route, the weather, the budget and the
                exports. Anything you change in the chat is saved to the studio straight away, and a small receipt
                under the reply tells you exactly what changed.
              </p>
              <Steps>
                <li><strong>Say where you're going.</strong> Your guide asks three quick things: where from, where to,
                  and how many days. Everything else is optional.</li>
                <li><strong>Pick how to plan.</strong> Get the whole itinerary written at once, or build it one day at
                  a time with your guide.</li>
                <li><strong>Open the studio</strong> to see the plan as days, a sketchbook, a map and a 3D route.</li>
                <li><strong>Change anything by asking.</strong> <q>Move the market to day 1.</q> It happens, and the
                  studio follows.</li>
                <li><strong>Take it with you</strong> as a PDF, a calendar, map files or a budget sheet.</li>
              </Steps>
              <div className="tv-howto__table" role="region" aria-label="Planning modes compared" tabIndex={0}>
                <table>
                  <thead>
                    <tr><th scope="col" /><th scope="col">Full itinerary</th><th scope="col">Build with your guide</th></tr>
                  </thead>
                  <tbody>
                    <tr><th scope="row">Best for</th><td>A complete first draft in about a minute</td><td>Shaping each day yourself, with a running budget</td></tr>
                    <tr><th scope="row">How it works</th><td>Researches the destination, then writes every day at once</td><td>Suggests places for one day at a time; you add, remove or move them</td></tr>
                    <tr><th scope="row">Changing it</th><td>Ask in the chat; the plan is revised and saved</td><td>Tap a suggestion or ask in the chat; each change is saved</td></tr>
                    <tr><th scope="row">Budget</th><td>Set a target, then fill in or accept suggested costs in the Budget planner</td><td>Tracked as you go. Going over gives you a heads-up; it never refuses your own request</td></tr>
                    <tr><th scope="row">Suggestions come from</th><td>Web research on your destination</td><td>Traveler posts on Reddit, Quora and TripAdvisor forums</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section id="pages">
              <h2>Finding your way around</h2>
              <p>Pathfinder has a handful of pages. The logo always takes you home, and the top bar reaches the rest.</p>
              <div className="tv-howto__table" role="region" aria-label="The pages of Pathfinder" tabIndex={0}>
                <table>
                  <thead><tr><th scope="col">Page</th><th scope="col">What it's for</th><th scope="col">How to get there</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">Home</th><td>What Pathfinder does, and where to start</td><td>The Pathfinder logo, from anywhere</td></tr>
                    <tr><th scope="row">Planner</th><td>The chat where you plan, with your trips listed beside it</td><td><strong>Plan a trip</strong> or <strong>Start planning</strong></td></tr>
                    <tr><th scope="row">Trip Studio</th><td>One page for a planned trip: plan, sketchbook, map, 3D, budget, exports</td><td><strong>Open studio</strong> in the planner (top right, the trip card, or any receipt)</td></tr>
                    <tr><th scope="row">My trips</th><td>Every trip you've started, to reopen or continue</td><td><strong>My trips</strong> in the top bar</td></tr>
                    <tr><th scope="row">Explore</th><td>Destinations to browse when you don't know where to go yet</td><td><strong>Explore</strong> in the top bar, or <strong>Explore destinations</strong> in the planner</td></tr>
                    <tr><th scope="row">Account</th><td>Your name and photo; signing in keeps your trips across devices</td><td><strong>Account</strong> in the top bar, or <strong>Sign in</strong></td></tr>
                    <tr><th scope="row">Guide</th><td>This page</td><td><strong>Guide</strong> in the top bar, or <strong>How to use Pathfinder</strong> in the planner and in the home page's menu</td></tr>
                    <tr><th scope="row">Credits</th><td>The data, photos, fonts and code Pathfinder is built on</td><td>The footer of the home page</td></tr>
                  </tbody>
                </table>
              </div>
              <Shot src="00-home" alt="The Pathfinder home page">
                <strong>Home.</strong> <strong>Start planning</strong> opens the planner, and the links in the middle
                jump to the sections of the page. The menu on the left opens My trips, Explore and this guide.
              </Shot>
              <Shot src="25-trips" alt="The My trips page listing two journeys">
                <strong>My trips.</strong> Every trip with where it stands. Pick one to see its summary on the right and
                reopen it. A trip you started but haven't briefed yet shows as an untitled journey.
              </Shot>
              <Shot src="24-explore" alt="The Explore page with destinations to browse">
                <strong>Explore.</strong> Destinations to browse for ideas before you have a trip in mind, filtered
                by kind. Picking one opens the planner, where you tell your guide where you'd like to go.
              </Shot>
              <Shot src="26-credits" alt="The Credits page listing data sources and their licences">
                <strong>Credits.</strong> Where the weather, holidays, exchange rates, maps and photos come from, each
                with its licence.
              </Shot>
              <p>Inside the planner, three places do the navigating:</p>
              <ul className="tv-howto__list">
                <li><strong>The sidebar</strong> (left): <strong>New trip</strong>, the trip you're on, your past trips, and
                  links to Explore and this guide. On a phone it's a drawer behind the menu button.</li>
                <li><strong>The top of the chat</strong>: <strong>Budget</strong> opens the budget planner, and once a plan
                  exists <strong>Open studio</strong> opens the studio. <q>Changes save to your studio</q> beside the
                  title is a reminder that there is no separate save step.</li>
                <li><strong>The studio's top bar</strong>: <strong>← Chat</strong> goes back, the tabs switch between Plan,
                  Sketch, Map and 3D, and <strong>Budget</strong> and <strong>Export</strong> sit on the right.</li>
              </ul>
              <p className="tv-howto__note">
                You don't need an account. A guest can use everything, and the trips stay in this browser. Sign in to
                keep them and open them on another device.
              </p>
            </section>

            <section id="start">
              <h2>Start a trip</h2>
              <Shot src="01-welcome" alt="The planner's opening screen with a greeting and four starter trips">
                The planner opens with a greeting (by name when you're signed in) and four starter trips. Type in the
                box at the bottom, or pick a starter.
              </Shot>
              <Steps>
                <li>Say where you want to go, as loosely or precisely as you like. We typed
                  <q>Plan 3 days in Kyoto from Delhi. I love food and old temples.</q></li>
                <li>Your guide replies with what it still needs (here it had everything, so it just asks you to check
                  the basics), and the <strong>trip brief</strong> opens one small step at a time. Whatever it
                  understood from your message is already filled in.</li>
                <li>Only the first step is required. <strong>Skip the rest</strong> is there from the start; <strong>Next
                  </strong> (or Enter) moves on; <strong>Back</strong> returns.</li>
              </Steps>
              <Shot src="02-brief-step-1" alt="Step 1 of the trip brief: from, to, days and a start date">
                <strong>Step 1 of 4: where and how long.</strong> Delhi, Kyoto and 3 days came from the message. A
                <strong> start date</strong> is optional, but it unlocks the weather, public holidays and the calendar
                export; the line under it shows the day you're back.
              </Shot>
              <Shot src="02b-brief-step-2" alt="Step 2 of the trip brief: travelers, comfort and budget">
                <strong>Step 2: who's coming and the budget.</strong> Travelers, comfort level, an optional total budget
                and its currency. Skip it and your guide plans for one adult, mid-range.
              </Shot>
              <Shot src="02c-brief-step-3" alt="Step 3 of the trip brief: pace, getting around, interests and things to avoid">
                <strong>Step 3: how you like to travel.</strong> Pace, how you get around, up to three interests and up
                to three things to avoid. When you build with your guide, an avoid becomes a rule it won't break
                without asking.
              </Shot>
              <Shot src="02d-guide-picker" alt="Step 4 of the trip brief: six guides to choose from">
                <strong>Step 4: pick your guide.</strong> Six guides, each with a one-line personality. Your guide chats
                with you, draws the sketchbook and signs the PDF cover. Leave <strong>Let this guide shape the plan</strong> on
                and the draft leans that way: quieter days, famous sights, meals, a tighter budget, lesser-known places,
                or late nights. Turn it off and the days ignore the guide. Your own pace, interests, and avoids still win.
              </Shot>
              <Shot src="03-planning-choice" alt="The planning choice with the saved brief and two ways to plan">
                The brief is saved and summed up on a card (<strong>Edit details</strong> reopens it on one page). Then
                choose: <strong>Generate full itinerary</strong> writes the whole draft now, and <strong>Build with
                …</strong> plans it day by day with your guide.
              </Shot>
            </section>

            <section id="one-shot">
              <h2>The full itinerary</h2>
              <p>
                <strong>Generate full itinerary</strong> researches the destination and streams a day-by-day plan into
                the chat in about a minute. A status line says what it's doing (<em>Researching possible stops</em>,
                <em> Writing your day-by-day draft</em>). With a start date, the draft also takes the season, the
                forecast and public holidays into account.
              </p>
              <Shot src="04-one-shot-itinerary" alt="The generated Kyoto itinerary in the chat">
                The draft: season notes for your month, an overview table, then each day from morning to evening. This
                trip starts on Sports Day, a public holiday in Japan, and day 1 says so.
              </Shot>
              <Shot src="05-trip-card" alt="The end of the itinerary and the trip card">
                Every draft ends with <strong>What to decide next</strong>. Below it sits the <strong>trip card</strong>:
                your dates, a photo of each place you're staying and <strong>Open studio</strong>. The card stays here, where the plan
                was made, while the conversation carries on underneath.
              </Shot>
            </section>

            <section id="changing">
              <h2>Changing the plan</h2>
              <p>
                This is the part to remember: <strong>to change the trip, say what you want.</strong> It is applied and
                saved at once. There is no draft to confirm and no <q>finalize</q> button. Under every reply that
                changed something, a <strong>receipt</strong> lists what actually changed, with <strong>Open studio
                </strong> beside it.
              </p>
              <Shot src="05b-change-receipt" alt="A revised itinerary ending in a receipt that lists the added place">
                We asked <q>Can you add the Arashiyama bamboo grove on the morning of day 2?</q> The reply is the
                whole plan, revised (this is the end of it). The receipt under it is the record: the grove was added
                to day 2. The draft already had it on day 3, and the revision kept that as an optional second look.
              </Shot>
              <p>
                If you ask for <strong>advice</strong> instead (<q>is there anything you'd move?</q>), your guide answers
                and offers one specific change. A plain <q>yes</q> applies it.
              </p>
              <Shot src="05c-offer" alt="A question answered with one specific offer">
                We asked <q>Day 1 looks busy. Is there anything you'd move to another day?</q> The plan isn't touched,
                so there is no receipt: the reply suggests one move (Nishiki Market to day 2) and asks whether to
                make it.
              </Shot>
              <Shot src="05d-yes-applied" alt="The plan after replying yes, with a receipt for the move">
                We replied <q>yes</q>. The market moved, and the receipt names the place and the days. The same
                revision dropped the second bamboo-grove visit from day 3, and the receipt lists that too: every
                line is something that changed in the saved plan, whether or not the reply mentions it.
              </Shot>
              <Shot src="05e-budget-from-chat" alt="A budget set from the chat with its receipt">
                The budget works the same way. <q>My budget is 150,000 INR</q> sets the trip's budget target without
                rewriting the plan, and the receipt confirms it. The Budget planner and the studio show the new target.
              </Shot>
              <ul className="tv-howto__list">
                <li><strong>Requests</strong> (<q>move</q>, <q>add</q>, <q>swap</q>, <q>remove</q>, <q>can you…</q>,
                  <q>how about…</q>) change the plan.</li>
                <li><strong>Questions</strong> (<q>is it crowded in the morning?</q>, <q>should I add Nara?</q>) get an
                  answer, and an offer when a change would help.</li>
                <li><strong>Changing the basics</strong> (<q>make it 5 days</q>, <q>start from Mumbai instead</q>)
                  researches the trip again and writes a fresh draft.</li>
                <li><strong>The receipt is the truth.</strong> It comes from the saved plan, not from the wording of
                  the reply. If it says <q>Nothing changed</q>, nothing did, whatever the reply sounded like.</li>
              </ul>
            </section>

            <section id="studio">
              <h2>The Trip Studio</h2>
              <p>
                <strong>Open studio</strong> turns the conversation into one page for the whole trip. <strong>← Chat
                </strong> takes you back. Each trip has its own address (<code>/trips/…</code>), so you can bookmark it.
              </p>
              <Shot src="06-studio-plan" alt="The Trip Studio on the Plan tab with the Days and Trip details windows">
                The <strong>Plan</strong> tab: a card per day with its places, exactly as the chat left them.
                <strong> Days</strong> on the left, <strong>Trip details</strong> on the right with the budget target set
                from the chat. The header shows the dates and travelers, with <strong>Budget</strong> and
                <strong> Export</strong> beside them.
              </Shot>
              <ul className="tv-howto__list">
                <li><strong>Tabs</strong>: <strong>Plan</strong> (the days as cards), <strong>Sketch</strong> (the
                  sketchbook), <strong>Map</strong> and <strong>3D</strong>. The arrow keys move between them.</li>
                <li><strong>Days</strong>: every day with a photo. Pick one and every tab follows it. Hover a photo for
                  its author and licence.</li>
                <li><strong>Trip details</strong>: the budget, your brief, the weather (a real forecast when your dates
                  are near, otherwise typical weather for the month), public holidays, exchange rates and
                  <strong> Around here</strong>: places near the day you've picked, each with <strong>Add to day</strong>.</li>
                <li><strong>Chat</strong>: the same conversation in a window, so you can keep planning while you watch
                  the plan change.</li>
              </ul>
              <p>
                The windows move, resize and close. Drag one against a side and the page makes room for it;
                <strong> Reset</strong> puts them all back.
              </p>
            </section>

            <section id="sketch">
              <h2>The sketchbook</h2>
              <p>
                <strong>Sketch</strong> draws your trip as hand-drawn pages, and your guide draws them in front of you:
                the first time you open a page, their pen inks the outlines, writes the labels and sticks the notes on.
                There's an overview, then one page per day.
              </p>
              <Shot src="07-studio-sketch" alt="The sketchbook overview: the route, preference stamps and the budget receipt">
                The overview: the route (one base here, Kyoto for days 1 to 3), your interests and things to avoid as
                stamps (<q>loves food</q>, <q>loves temples</q>, <q>no crowded attractions</q>), and a budget receipt.
              </Shot>
              <Shot src="07c-sketch-day" alt="A finished day page">
                A day page: morning, afternoon and evening, each place with a doodle for its kind (a gate for the
                shrine, a bag for the food hall), an arrow in visiting order, the public holiday as a flag, and the
                day's weather in the corner. This trip is two weeks away, so it says <q>Typical for October</q>
                rather than a forecast.
              </Shot>
              <ul className="tv-howto__list">
                <li><strong>Only what's in your plan</strong>: every name, tip and number comes from the trip. Tips from
                  traveler posts are tagged with where they came from (<q>via tripadvisor</q>).</li>
                <li><strong>Turn pages</strong> with the arrows, the arrow keys, Page Up/Down, Home/End, or a swipe. The
                  page follows the day you pick in <strong>Days</strong>.</li>
                <li><strong>Redraw</strong> watches your guide draw the page again. <strong>Draw</strong> lets you add
                  your own notes on top of a page; they're saved with the trip.</li>
                <li>With <em>reduce motion</em> turned on in your system settings, pages appear finished and the guide
                  stays still.</li>
              </ul>
            </section>

            <section id="map">
              <h2>Map &amp; 3D view</h2>
              <p>
                <strong>Map</strong> and <strong>3D</strong> show the same trip as a route: where you start, each base,
                and the legs between them. They follow the day you pick in <strong>Days</strong>, and they redraw after
                every change.
              </p>
              <Shot src="08-studio-map" alt="The Map tab with the route into Kyoto">
                <strong>Map</strong>: the route on a real map. Switch <strong>Map / Terrain / Satellite</strong>, and
                <strong> Stops</strong> or the <strong>Full route</strong>. Pick a stop or a leg to open its details; the
                panel underneath carries a heads-up about the season.
              </Shot>
              <Shot src="09-studio-3d" alt="The 3D tab: the trip as a route between Delhi and Kyoto">
                <strong>3D</strong>: the trip as a route you can orbit, with travel time and distance on each leg (here
                Delhi to Kyoto, about 7 h 20 m and 5,496 km). <strong>Atlas / Outline / Journal</strong> switch the
                layout.
              </Shot>
            </section>

            <section id="export">
              <h2>Export &amp; PDF</h2>
              <p>
                <strong>Export</strong> (top right in the studio) takes the trip anywhere. Pick a file and it saves.
                When a file takes a while to prepare, the menu says it's ready and shows a <strong>Save</strong> button
                instead: one more click, and it's yours.
              </p>
              <Shot src="10-export-menu" alt="The Export menu open in the studio">
                The Export menu, with a Google Maps directions link for each day at the bottom. Behind it, the Plan tab
                with a day open: pick a day in <strong>Days</strong> and the plan shows it slot by slot.
              </Shot>
              <Shot src="10b-export-ready" alt="The Export menu showing a file that is ready, with a Save button">
                A file waiting to be saved: <q>Trip data is ready</q> and a <strong>Save</strong> button with the file's
                name. After a normal download the same spot says <q>downloaded</q>, with <strong>Save it again</strong>.
              </Shot>
              <ul className="tv-howto__list">
                <li><strong>PDF</strong>: a cover with your guide, every sketch page (sharp at any zoom), the day-by-day
                  plan with tips and linked sources, the weather and holidays, the budget, and credits.</li>
                <li><strong>Calendar (.ics)</strong>: an event for each planned place, timed by morning, afternoon or
                  evening, plus holidays. A trip without dates gets a <strong>Trip starts on</strong> field right in
                  the menu: pick the day and the calendar is ready.</li>
                <li><strong>Google Maps</strong>: directions for each day, up to 9 stops.</li>
                <li><strong>GPX</strong> and <strong>KML</strong>: pins for Organic Maps, Maps.me, Google My Maps and GPS
                  apps. The first time, Pathfinder looks up where each place is, which can take up to a minute.</li>
                <li><strong>Budget (.csv)</strong> and <strong>Everything (.json)</strong>: the budget for a
                  spreadsheet, and the whole trip as data.</li>
              </ul>
            </section>

            <section id="budget">
              <h2>Budget planner</h2>
              <p>
                <strong>Budget</strong> (at the top of the chat or the studio) opens the trip's costs. The three boxes at
                the top say how it works: set a target, fill in the costs, watch what's left. Rows come from the
                itinerary: for each base you get stay, food, activities and local travel, plus one row per journey
                between places. Stay counts <em>nights</em>; food and local travel count <em>days</em>.
              </p>
              <Shot src="11-budget-empty" alt="The budget planner before any amounts are entered">
                The budget planner for the Kyoto trip, before any amounts. The target came from the chat
                (<q>My budget is 150,000 INR</q>); you can also type it here and press <strong>Save target</strong>.
              </Shot>
              <Shot src="12-budget-suggestions" alt="The budget with suggested amounts and a projected trip cost">
                After <strong>Suggest amounts</strong>: each row gets a suggestion and where it came from (for example
                <q>Reddit travelers</q>). <strong>Projected trip cost</strong> adds them up (about ₹98,520 here,
                against the ₹150,000 target). Suggestions stay out of your total until you use them.
              </Shot>
              <Shot src="13-budget-rows" alt="Budget rows with Use and Edit actions">
                <strong>Use</strong> accepts one suggestion, <strong>Use all suggestions</strong> accepts the lot, and
                <strong> Edit</strong> sets your own amount or quantity. Journeys between places get their own rows
                (here the flights from Delhi to Kyoto and back). <strong>Add a cost</strong> covers anything else:
                insurance, visas, tickets.
              </Shot>
            </section>

            <section id="agent">
              <h2>Build with your guide</h2>
              <p>
                Choose <strong>Build with …</strong> (your guide's name) on the planning card. There's no extra setup: the
                budget, pace, travel mode and interests come from your trip brief, and you can change any of them later
                just by saying so.
              </p>
              <Steps>
                <li>Your guide reads traveler posts for your destination, estimates typical hotel, food and transit
                  costs, and ranks places for each day. The first reply takes a little longer than later ones.</li>
                <li>The reply opens day 1, and the <strong>day dock</strong> appears above the message box.</li>
              </Steps>
              <Shot src="14-build-day-1" alt="The first reply for day 1 with the day dock under it">
                Day 1 of our Tokyo trip with Octa-san. The dock is one line: the day you're on with arrows to change it, how
                many places and hours are planned (<q>Open day · 0 of 8 h</q>), and the trip budget with its bar. Under
                it are the day's <strong>suggestions</strong>, each with its typical cost (these gardens are free).
                Stay and food are already counted in the ~¥39,000; flights live in the Budget planner.
              </Shot>
              <Shot src="15-build-added" alt="Day 1 after tapping a suggestion, with a receipt">
                Tapping a suggestion sends it for you (<q>Add Kyu-Yasuda Garden to day 1</q>) and adds it to the day
                on screen. The receipt confirms it, the dock now reads <q>1 place · 2 of 8 h</q>, and a new
                suggestion takes the used one's place.
              </Shot>
            </section>

            <section id="agent-dock">
              <h2>The day dock and the day plan</h2>
              <p>
                The dock stays small so the conversation keeps the screen. Each day has its own plan and its own
                suggestions, and switching days with the arrows is instant: it doesn't ask your guide anything.
              </p>
              <Shot src="16-build-day-plan" alt="The day dock opened to show every day and the places on this one">
                <strong>Day plan</strong> opens the rest: a tab for every day, the places on this one with
                <strong> Remove</strong>, and <strong>Finish itinerary</strong>. The note underneath says how much of the
                total is stay and food, and that flights are counted in the Budget planner.
              </Shot>
              <Shot src="17-build-preferences" alt="A typed request that sets a rule and asks for a lunch spot">
                Typing works too, and it applies to the day on screen. We typed <q>I never want museums on this trip.
                Also add a great local lunch spot today.</q> The receipt shows both halves: the rule was saved, and
                a lunch spot (Ootoya) was added to day 2. This shot predates a fix: the reply also claims Omide
                Yokocho was added, though it is still only a suggestion in the dock. Replies are now checked against
                the receipt before you see them, and a sentence claiming a change that didn't happen is left out.
              </Shot>
              <Shot src="17b-build-move" alt="A place moved to another day, with a receipt">
                <q>Move Kyu-Yasuda Garden to day 3</q> moves it, even though the dock is on day 2. You can see your
                message, the reply, the receipt and the next suggestions together, which is the point of keeping the
                dock small.
              </Shot>
            </section>

            <section id="agent-rules">
              <h2>Rules, heads-ups and "Add anyway"</h2>
              <p>
                When you build with your guide, <strong>what you ask for happens.</strong> The planner still watches
                three things, and tells you about them instead of getting in your way.
              </p>
              <ul className="tv-howto__list">
                <li><strong>Day length.</strong> If a move or an add takes a day past its hours for your pace, the
                  change goes through and the receipt carries a <strong>heads-up</strong> (<q>day 2 is now 11 h, over
                  the 8 h a balanced day holds</q>).</li>
                <li><strong>Budget.</strong> Same again: the change is made, and the heads-up says how far over budget
                  the trip now is. Your guide's own <em>suggestions</em> always stay within your hours and budget.</li>
                <li><strong>Your own rules.</strong> Things you said to avoid, and places you turned down, are the one
                  case where the planner asks first.</li>
              </ul>
              <Shot src="18-build-add-anyway" alt="A request held back by the traveler's own rule, with an Add anyway button">
                After <q>I never want museums</q>, we asked <q>Add the Tokyo National Museum to day 2</q>. Nothing was
                added: the receipt is headed <q>Nothing changed</q> and says why, and <strong>Add anyway</strong>
                pushes it through in one tap. (Typing <q>add it anyway</q> does the same.)
              </Shot>
              <p>Your guide keeps two kinds of preferences:</p>
              <ul className="tv-howto__list">
                <li><strong>Rules</strong>: must-sees and things to avoid (<q>never</q>, <q>can't</q>, allergies,
                  mobility). Places matching an avoid are never suggested.</li>
                <li><strong>Leanings</strong>: likes and dislikes (<q>I prefer quiet places</q>, <q>not a big fan of
                  shopping</q>). They move places up or down the list without ruling anything out.</li>
                <li><strong>Turned-down places</strong>: say <q>remove it, and don't suggest it again</q> and it's gone
                  for the whole trip.</li>
              </ul>
              <p className="tv-howto__note">
                If your hotel and food alone already use up the budget, your guide says so and offers the fixes: raise
                the budget, or tell it your real costs, for example <q>my hotel is 6000 yen a night</q>.
              </p>
            </section>

            <section id="connected">
              <h2>How it all connects</h2>
              <p>The chat, the Budget planner, the studio and the sketchbook are one trip seen four ways, and they stay in sync.</p>
              <ul className="tv-howto__list">
                <li><strong>Chat → studio.</strong> Every change is saved as it's made. Keep the chat open in the studio
                  and you watch it land: your guide turns to the day that changed and draws only what's new.</li>
                <li><strong>Guide → Budget planner.</strong> The stay, food, activity and local-travel numbers your guide
                  plans with appear in the Budget planner as suggestions marked <q>From your build-with-agent
                  plan</q>.</li>
                <li><strong>Budget planner → guide.</strong> Amounts you type for stay or food replace the estimates on
                  your next message, so the running total uses your real prices.</li>
                <li><strong>Flights and other journeys</strong> between cities are only in the Budget planner. Use
                  <strong> Suggest amounts</strong> there to estimate them.</li>
              </ul>
              <Shot src="19-build-budget-sync" alt="The budget planner for the Tokyo trip showing the guide's numbers as suggestions">
                The Tokyo budget planner: the ¥60,000 target came from the brief, and the rows carry your guide's own
                numbers as suggestions, adding up to the same ~¥40,200 the dock shows. <strong>Suggest the rest
                </strong> estimates the two rows your guide doesn't plan: the flights.
              </Shot>
              <Shot src="20-build-live-sketch" alt="The studio with the chat open while the guide draws a new place">
                The studio with the chat open. We typed <q>Add a quiet garden to day 1 if it fits.</q> While your guide
                is still writing, Kiyosumi Teien is drawn onto day 1, and a note beside your guide says what was
                added.
              </Shot>
              <Shot src="21-build-live-sketch-done" alt="The same page after the change is drawn, with its receipt in the chat window">
                A moment later: the new place and its traveler tip are on the page, and the chat window shows the
                reply with its receipt. In here the receipt reads <q>Studio updated</q>, since you're already looking
                at the studio.
              </Shot>
              <Shot src="22-build-complete" alt="The wrap-up after finishing the itinerary">
                <strong>Finish itinerary</strong> (in the day plan, or just say <q>finish</q>) wraps up with a summary
                of each day and the budget. A finished trip isn't locked: keep asking, and keep tapping suggestions.
              </Shot>
            </section>

            <section id="phone">
              <h2>On your phone</h2>
              <p>
                On a phone, the trip list becomes a drawer (open it with the menu button) so the chat gets the whole
                screen. In the studio, the windows become bottom sheets, one at a time, and the sketchbook switches to
                upright pages that fill the screen.
              </p>
              <Shot src="23b-phone-chat" alt="The planner on a phone: a reply, its receipt, the day dock and the message box" portrait>
                The chat on a phone: the reply, its receipt, the day dock and the message box all fit. The two buttons
                at the top right are <strong>Budget</strong> and <strong>Open studio</strong>.
              </Shot>
              <Shot src="23-phone-sketch" alt="A portrait sketch page on a phone" portrait>
                A sketch page on a phone: the page stands upright, the time slots stack, and the notes run along the
                bottom. Swipe to turn pages.
              </Shot>
            </section>

            <section id="phrases">
              <h2>Things you can say</h2>
              <div className="tv-howto__table" role="region" aria-label="Example phrases" tabIndex={0}>
                <table>
                  <thead><tr><th scope="col">You say</th><th scope="col">What happens</th></tr></thead>
                  <tbody>
                    <tr><td><q>Move Nishiki Market to day 1</q> · <q>Swap days 2 and 3</q></td><td>Moves it. The receipt shows what moved, with a heads-up if a day is now over its hours</td></tr>
                    <tr><td><q>Add teamLab Planets to day 3</q> · <q>Add the 2nd one</q></td><td>Adds it to that day</td></tr>
                    <tr><td><q>Yes</q> · <q>Do it</q> · <q>Go ahead</q></td><td>Applies the change your guide just offered</td></tr>
                    <tr><td><q>Move it to day 5</q> · <q>The second option</q></td><td><q>It</q> is whatever you and your guide were just talking about</td></tr>
                    <tr><td><q>Add it anyway</q></td><td>Pushes through something your own rule held back</td></tr>
                    <tr><td><q>Remove Golden Gai and don't suggest it again</q></td><td>Removes it for good</td></tr>
                    <tr><td><q>I can't do long hikes</q> · <q>Tokyo Tower is a must</q></td><td>Saves a rule (avoid / must-see)</td></tr>
                    <tr><td><q>I'm more into cafés than bars</q></td><td>Saves a leaning (like / dislike)</td></tr>
                    <tr><td><q>My budget is 60,000 INR</q> · <q>Raise the budget to 45000</q></td><td>Sets the budget target, in the chat and the Budget planner</td></tr>
                    <tr><td><q>My hotel is 6000 yen a night</q> · <q>Food is about 3000 a day</q></td><td>Replaces those estimates (build with your guide)</td></tr>
                    <tr><td><q>We'll take taxis</q> · <q>Let's make it relaxed</q></td><td>Changes travel mode or pace</td></tr>
                    <tr><td><q>Find late-night ramen near Shinjuku</q></td><td>Searches traveler posts for new ideas</td></tr>
                    <tr><td><q>Is Fushimi Inari crowded early?</q> · <q>Should I add Nara?</q></td><td>An answer, and an offer if a change would help</td></tr>
                    <tr><td><q>Finish</q> · <q>That's final</q></td><td>Wraps up. Everything was already saved</td></tr>
                  </tbody>
                </table>
              </div>
            </section>

            <section id="faq">
              <h2>Questions &amp; fixes</h2>
              <dl className="tv-howto__faq">
                <dt>How do I finalize or save the plan?</dt>
                <dd>You don't have to. Every change is saved to the studio the moment it's made, and the receipt under
                  the reply confirms it. <q>Finish</q> only writes a wrap-up.</dd>
                <dt>The reply said it changed something, but I see no receipt.</dt>
                <dd>Then nothing changed. The receipt comes from the saved plan; trust it over the wording of a reply.
                  Ask again more plainly, for example <q>move Nishiki Market to day 1</q>.</dd>
                <dt>I asked for a change and got a question back.</dt>
                <dd>Your message read as a request for advice, so your guide offered a change instead of making it.
                  Reply <q>yes</q>, or phrase it as a request: <q>move …</q>, <q>add …</q>, <q>can you …</q>.</dd>
                <dt>My export didn't download.</dt>
                <dd>Open <strong>Export</strong> again: a file that took a while waits behind a <strong>Save</strong> button.
                  If your browser asks whether this site may download several files, allow it.</dd>
                <dt>Why is Calendar greyed out in Export?</dt>
                <dd>A calendar needs dates. Pick a day in the <strong>Trip starts on</strong> field under it and it
                  becomes available.</dd>
                <dt>Are the prices real quotes?</dt>
                <dd>No. They're typical amounts from traveler reports and estimates, always marked with ~ or
                  <q>Suggested</q>. Nothing is booked. Enter what you actually pay in the Budget planner.</dd>
                <dt>Is the weather a forecast?</dt>
                <dd>Only when your trip is near, and then it says <q>Forecast</q>. Further out it's typical weather for
                  that month, labelled <q>Typical for …</q>.</dd>
                <dt>The total is over budget before I've added anything.</dt>
                <dd>When you build with your guide, the total includes hotel nights and daily food for the whole trip.
                  Raise the budget, or tell your guide your real hotel and food costs.</dd>
                <dt>Does switching days lose my plan?</dt>
                <dd>No. Every day keeps its plan. Switching is instant and doesn't message your guide.</dd>
                <dt>Nothing was suggested for a day.</dt>
                <dd>Say what you're in the mood for (<q>street food and a quiet garden</q>). Your guide notes it and
                  searches traveler posts for that day.</dd>
                <dt>Can I change my guide or the brief?</dt>
                <dd>Yes, with <strong>Edit details</strong> on the planning card, before you pick how to plan.</dd>
                <dt>Where do the photos come from?</dt>
                <dd>Wikimedia Commons. Hover a photo for its author and licence; every source is on the Credits page.</dd>
              </dl>
            </section>

            <section id="whats-new">
              <h2>What's new</h2>
              <ul className="tv-howto__list tv-howto__changes">
                <li><strong>{LAST_UPDATED}.</strong> <strong>Changes you ask for now happen</strong>, with a receipt under
                  each reply and <strong>Open studio</strong> beside it; <q>yes</q> applies an offer; the budget can be
                  set from the chat. The build-with-your-guide panel became a one-line <strong>day dock</strong>, so
                  the conversation keeps the screen. The trip brief comes in <strong>four small steps</strong> with
                  Skip the rest, and the planner opens with a greeting. <strong>Exports</strong> that take a while wait
                  behind a Save button, and the calendar works for trips without dates. This guide was rebuilt.</li>
                <li><strong>September 29, 2026.</strong> The <strong>Trip Studio</strong>: plan, sketchbook, map and 3D on
                  one page, with movable Days, Trip details and Chat windows. Guides who draw your trip as a
                  <strong> hand-drawn sketchbook</strong>. Exports: PDF, calendar, Google Maps, GPX, KML, CSV and JSON.
                  Weather, public holidays and exchange rates for your dates.</li>
                <li><strong>September 28, 2026.</strong> Replies stream in, and each change appears before the reply
                  finishes. The route map and 3D view were redesigned, with three layouts (Atlas, Outline, Journal).</li>
              </ul>
            </section>
          </article>
        </div>
      </main>
    </div>
  );
}
