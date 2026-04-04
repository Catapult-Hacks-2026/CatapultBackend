4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
I N T E R N A L T E C H N I C A L S P E C I F I C AT I O N · R E V 3
Galileo API
Endpoints & Types
Complete specification of all required backend endpoints and TypeScript data
types for the Galileo autonomous procurement dashboard. Includes updated
company architecture separating global supplier data from enterprise-specific
analytics derived from linked events.
VERSION
3.0
DATE
April 4, 2026
ENDPOINTS
14
TYPES
23
T E R
M I N O L O G Y K E Y
Enterprise Company / Supplier Location Agent Event Linked Event Enterprise Company
View
The corporate client using Galileo (e.g. a Fortune 500 company procuring travel).
A hotel chain or airline. Global entity — not owned by any single enterprise.
A specific property or hub of a Company (e.g. "Chicago River North" branch of Hilton).
General info only.
An autonomous Galileo negotiation agent executing a single call/email thread with one
Supplier on behalf of an Event.
A corporate travel or meeting program requiring hotel and/or airline procurement.
An event tied to a company because it has an agent targeting that company with a status
of "Negotiating" or an accepted offer.
The analytics overlay for a specific enterprise's relationship with a company — derived
entirely from that enterprise's linked events.
C O
M PA N Y A R C H I T E CT U
R E
Companies are global entities. A Company (hotel chain, airline) exists independently of any enterprise. Its
general profile — name, locations, addresses, contact info — is shared and accessible to all enterprises without
authentication scoping.
Enterprise-specific data is always derived from linked events. A company is "linked" to an enterprise when
the enterprise has an agent targeting that company whose offer was accepted, or whose status is currently
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 1/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
"Negotiating". All enterprise-specific metrics — total savings, pricing trends, booking window scores, avg delta,
bookings — are computed from these linked events, not stored directly on the Company.
The Companies screen was simplified to a f
ull directory with a search bar. No filters. All companies are shown
regardless of whether the viewing enterprise has a relationship with them.
Endpoints
GET /enterprises/{enterpriseId}
Get Enterprise
Returns the authenticated enterprise's profile and aggregate procurement performance. Root data call for all
savings totals.
USED ON
DashboardPage — "Total Saved This Year", hotel/airline breakdown, YoY% SettingsPage — enterprise identity
RESPONSE → E N T E R P R I S E
GET /enterprises/{enterpriseId}/agents
Get All Agents
Returns all negotiation agents belonging to the enterprise across all events and suppliers. Supports status filtering.
QUERY PARAMS
PARAM TYPE REQUIRED DESCRIPTION
status AgentStatus — Filter by agent status
eventId string — Filter agents for a specific event
limit number —Max results (default 50)
USED ON
DashboardPage — agent negotiations table (first 3) RESPONSE → A G E N T [ ]
AllAgentsPage — f
ull table + status stat cards
GET /agents/{agentId}
Get Agent Detail
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 2/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
Returns a single agent with f
ull negotiation history, price path, activity stream snapshot, and transcript. Primary
data source for the live negotiation view.
USED ON
NegotiationAgentPage — all price data, status chip, price path chart, action card
RESPONSE → A G E N T (FULL, WITH P R I C E P A T H , P R E V I O U S N E G O T I A T I O N S , A C T I V I T Y S T R E A M , T R A N S C R I P T )
GET /enterprises/{enterpriseId}/events
Get All Events
Returns all events for the enterprise. EventsPage splits results into Active and Completed sections, renders per-
agent status badges and accept buttons inline.
QUERY PARAMS
PARAM TYPE REQUIRED DESCRIPTION
status EventStatus — "Active" | "Completed"
USED ON
EventsPage — Active and Past event lists, per-agent status, inline Accept Offer
DashboardPage — event name links in agent rows
RESPONSE → G A L I L E O E V E N T [ ]
GET /events/{eventId}
Get Event Detail
Returns a single event with f
ull agent list. For completed events also includes transcript and price path of the
winning agent.
USED ON
EventDetailPage — event metadata, agent cards, accept-offer logic, winner display, transcript
RESPONSE → G A L I L E O E V E N T (FULL)
GET /companies
Get All Companies
Returns the global directory of all supplier companies (hotels and airlines). Not enterprise-scoped. The
Companies screen shows every company in the system with a client-side search bar — no filters, no enterprise
context required. The q param enables server-side search for f
uture scale.
QUERY PARAMS
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 3/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
PARAM TYPE REQUIRED DESCRIPTION
q string — Search query — matches against company name
limit number —Max results (default 100)
USED ON
CompaniesPage — f
ull company grid with search bar
RESPONSE → C O M P A N Y [ ] (GENERAL FIELDS ONLY — NO ENTERPRISE-SPECIFIC ANALYTICS)
Architecture note: This endpoint returns no enterprise-specific data. Savings, bookings, pricing trends, and
booking window are not included here. Those come from GET /enterprises/{id}/companies/{companyId}.
GET /companies/{companyId}
Get Company General Info
Returns the general, non-enterprise-specific profile of a company. Includes identity fields, location list with
addresses and contact info, but no enterprise analytics — no savings figures, pricing trends, or booking window.
This data is the same regardless of which enterprise is viewing.
USED ON
CompanyDetailPage — company header (name, initials, description, phone, website, location chips)
RESPONSE → C O M P A N Y (GENERAL, WITH L O C A T I O N S [ ] CONTAINING ONLY I D , N A M E , A D D R E S S , P H O N E )
GET /enterprises/{enterpriseId}/companies/{companyId}
Get Enterprise Company View
Returns the enterprise-specific analytics overlay for a given company, computed from all events where this
enterprise has an agent targeting this company with status "Negotiating" or an accepted offer. This is the data
that populates the CompanyDetailPage analytics section — savings KPIs, pricing trends chart, booking window
scores, and the linked events table.
The backend derives all values dynamically from linked events — no separate analytics store is needed.
QUERY PARAMS
PARAM TYPE REQUIRED DESCRIPTION
locationId string — Scope analytics to a specific location. If omitted, returns portfolio-wide
aggregates.
USED ON
CompanyDetailPage — "Total Lifetime Savings" KPI, savings delta, agreements summary
CompanyDetailPage — Pricing Trends chart (1Y and ALL ranges)
CompanyDetailPage — Booking Window widget (month scores) CompanyDetailPage — Current And Past Events table
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 4/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
RESPONSE → E N T E R P R I S E C O M P A N Y V I E W
Linked event criteria: An event is linked to a company for an enterprise when that enterprise has an agent on the
event where agent.companyId === companyId AND (agent.isAccepted === true O
R agent.status ===
"Negotiating").
POST /events/{eventId}/agents/{agentId}/accept
Accept Offer
Marks the specified agent's offer as accepted. Backend atomically: sets isAccepted = true, cancels all other
agents of the same service type on this event, increments enterprise savings totals, and transitions the event to
Completed if all required service types now have an accepted offer. Because this creates a new linked event for
the company, the enterprise company
view
for that company will update on next fetch.
REQUEST BODY → A C C E P T O F F E R R E Q U E S T
USED ON
EventsPage — inline Accept Offer buttons EventDetailPage — agent card Accept Offer button
NegotiationAgentPage — primary CTA when status is "Completed"
RESPONSE → G A L I L E O E V E N T (UPDATED)
Side effects: competing same-type agents set to Cancelled; enterprise totalSaved incremented; event may
flip to "Completed"; EnterpriseCompanyView for the accepted company is now
updated (new linked event).
POST /agents/{agentId}/intervene
Intervene Manually
Pauses the autonomous agent and reroutes the live call or email thread to a human rep. Only available when agent
status is "Negotiating".
USED ON
NegotiationAgentPage — "Talk to Hotel/Airline Rep" button SupportPage — escalation routing
RESPONSE → I N T E R V E N T I O N R E S U L T
SSE /agents/{agentId}/activity-stream
Live Stream — Agent Activity
Server-Sent Events stream pushing ActivityStreamItem events in real time as the agent progresses through
negotiation rounds. Rendered as a live timeline on NegotiationAgentPage.
USED ON
NegotiationAgentPage — live activity timeline
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 5/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
STREAM EVENT → A C T I V I T Y S T R E A M I T E M
SSE /agents/{agentId}/transcript
Live Stream — Negotiation Transcript
Server-Sent Events stream pushing Message objects as the negotiation conversation progresses. Galileo messages
render on the left, supplier Rep messages on the right.
USED ON
NegotiationAgentPage — live transcript (last 2 + expand) EventDetailPage — f
ull transcript for completed events
STREAM EVENT → M E S S A G E
POST /market/pricing
Get Market Rate & Expected Negotiated Price
Given service type, location, date range, and attendee count, returns the current market rate and Galileo's
predicted win price. Populates the market analysis cards on NegotiationShellPage before the user sets guardrails.
REQUEST BODY
FIELD TYPE REQUIRED DESCRIPTION
service ServiceType ✓ "Hotel" | "Airline" | "Both"
location string ✓ City or airport code
startDate string (ISO) ✓ Event start date
endDate string (ISO) ✓ Event end date
attendees number ✓ Number of attendees
USED ON
NegotiationShellPage — market analysis cards, predicted win price
RESPONSE → M A R K E T P R I C I N G R E S U L T
POST /negotiations/launch
Launch Negotiations
Creates a new Event, resolves all matching hotels and/or airlines in the target area, and enqueues or immediately
launches autonomous agents for each. Returns the newly created event with all queued agents.
REQUEST BODY → L A U N C H N E G O T I A T I O N R E Q U E S T
FIELD TYPE REQUIRED DESCRIPTION
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 6/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
enterpriseId string ✓ Owning enterprise
eventName string ✓ Display name for the event
service ServiceType ✓ "Hotel" | "Airline" | "Both"
startDate string (ISO) ✓
endDate string (ISO) ✓
location string ✓ City or venue
attendees number ✓
budgetPerPerson number — Optional per-person budget
requirements string — Free-text procurement requirements
guardrails.hotel.idealPrice number — Anchor target per night
guardrails.hotel.ceilingPrice number —Max acceptable per night
guardrails.airline.idealPrice number — Anchor target per seat
guardrails.airline.ceilingPrice number —Max acceptable per seat
USED ON
NegotiationShellPage — "Launch Agent" buttonMarketInsightsResultsPage — "Launch Negotiation" buttons
RESPONSE → G A L I L E O E V E N T (NEWLY CREATED, AGENTS IN " Q U E U E D "
R
O
" N E G O T I A T I N G " STATUS)
POST /market/event-window
Find Best Event Window
Queries historical rate compression data to identify optimal time windows for an event. Returns 3 ranked
recommendation windows (Best Overall, Backup, Budget) each with expected pricing and a negotiation confidence
score.
REQUEST BODY
FIELD TYPE REQUIRED DESCRIPTION
location string ✓ Target city or region
eventType string ✓ e.g. "Company Retreat", "Sales Kickoff"
preferredTiming string ✓ e.g. "Q4 2026"
attendees number ✓
nights number ✓ Duration in nights
eventDetails string — Free-text requirements
USED ON
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 7/17
4/4/26, 12:22 PM MarketInsightsPage — "R
RESPONSE → E V E N T W I N D O W R E S U L T [ ] Catapult Galileo — API Endpoints & Types
un Timing Analysis" buttonMarketInsightsResultsPage — 3 recommendation window cards
(ARRAY OF 3)
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 8/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
Types
Enums & Union Types
AgentStatus ENU
M
VALUE MEANING WHERE SHOWN
"Negotiating" Agent is actively in a live
call/email exchange
DashboardPage, AllAgentsPage, NegotiationAgentPage; also makes
this event a linked event for the company
"Reviewing" Agent reached a counter-offer
awaiting review
DashboardPage, AllAgentsPage
"Optimized" Contract locked at target price DashboardPage, AllAgentsPage
"Completed" Negotiation finished; offer
ready to accept or reject
EventDetailPage, NegotiationAgentPage — enables Accept Offer
button
"Cancelled" Cancelled after a competing
offer was accepted
EventDetailPage agent cards
"Queued" Created but not yet started Immediately after Launch Negotiations
ServiceTypeENU
M
VALUE MEANING WHERE USED
"Hotel" Hotel room block procurement
only
ConfigureNegotiationPage toggle, Agent type badge, Event.service
"Airline" Airline seat procurement only Same as above
"Both"Launches hotel and airline
agents
ConfigureNegotiationPage, NegotiationShellPage (shows two guardrail
inputs)
EventStatus ENU
M
VALUE MEANING WHERE USED
"Active" Has agents negotiating or pending
acceptance
EventsPage "Active Events" section, live ping dot
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 9/17
4/4/26, 12:22 PM "Completed" Catapult Galileo — API Endpoints & Types
All required service types have an accepted
offer
EventsPage "Past Events", EventDetailPage winner
display
PricePointTypeENU
M
VALUE CHART COLO
R
MEANING
"offer" Orange Supplier's counter-offer price
"negotiated" Blue Galileo's negotiated bid
"current" GreenMost recent / current price
M
BookingWindowStatus ENU
VALUE SCO
RE RANGE MEANING
"Best Deal" 75 – 100 Historically lowest rates, highest compression
"Good" 45 – 74 Below-market rates achievable
"Peak" 0 – 44 High demand, limited negotiation leverage
Core Types
Enterprise CO
RE
FIELD TYPE DESCRIPTION
id stringUnique enterprise identifier
name string Enterprise display name
description string Brief company description
totalSavedHotels numberLifetime hotel savings — DashboardPage "Hotels" breakdown
totalSavedAirlines numberLifetime airline savings — DashboardPage "Flights" breakdown
totalSaved number Sum total — DashboardPage hero metric
yoyChange number Year-over-year savings % — DashboardPage YoY badge
hotelContractCount number Active hotel contracts — DashboardPage label
airlineContractCount number Active airline contracts — DashboardPage label
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 10/17
4/4/26, 12:22 PM Company CO
Catapult Galileo — API Endpoints & Types
RE Global — not enterprise-scoped
FIELD TYPE DESCRIPTION
id stringUnique company ID — used in route /companies/:id and as agent.companyId
name string Display name — CompaniesPage card, CompanyDetailPage header, search matches
against this
initials string 1–2 letter avatar label — AvatarMark on company cards and detail page
description string Company bio — CompanyDetailPage header paragraph
phone string Primary contact — CompanyDetailPage header
website string Website — CompanyDetailPage header
industry string "Hospitality" | "Aviation" | "Logistics"
badge string "Strategic Partner" | "Preferred Supplier" | "Standard" — CompaniesPage card badge
locations Location[] All supplier properties — drives location chips on CompanyDetailPage. General info only
(name, address, phone).
No enterprise-specific fields. totalSavings, yoyChange, avgDelta, totalBookings, pricingTrends, and
bookingWindow are NOT on this type. They live on EnterpriseCompanyView, derived from linked events.
EnterpriseCompanyView DERIVED Computed from linked events — enterprise-scoped
FIELD TYPE DESCRIPTION
companyId stringReference to the base Company
enterpriseId string The enterprise this view
belongs to
locationId string? If scoped to one location; null means portfolio-wide
lifetimeSavings number Total savings across all accepted offers with this company —
CompanyDetailPage "Total Lifetime Savings" KPI
savingsDelta number YoY change in savings % — CompanyDetailPage savings delta badge
agreementsCount number Number of accepted-offer events with this company
agreementsSummary string Human-readable description — CompanyDetailPage savings card text
avgDelta number Average negotiation delta % across linked events — CompaniesPage
card stat
totalBookings number Total bookings across linked events — CompaniesPage card stat
totalSavings number Aggregate savings — CompaniesPage card "Total Savings Realized"
yoyChange number YoY savings % — CompaniesPage card YoY badge
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 11/17
4/4/26, 12:22 PM pricingTrends PricingTrend[] bookingWindow BookingWindowEntry[] linkedEvents GalileoEvent[] Catapult Galileo — API Endpoints & Types
Historical negotiated vs market prices derived from linked event
agent prices — CompanyDetailPage chart
12-month booking quality scores derived from historical linked event
timing — CompanyDetailPage widget
Events with accepted or currently-negotiating agents for this
company — CompanyDetailPage events table
Location CO
RE A property or hub — general info only
FIELD TYPE DESCRIPTION
id stringUnique location ID — used as the location chip key and as the locationId query param for GET
/enterprises/{id}/companies/{companyId}
companyId string Parent company reference
name stringLocation display name — CompanyDetailPage location chips (e.g. "London Executive Campus")
address string Full street address
phone stringLocation-specific contact number
No analytics fields. pricingTrends and bookingWindow are not on Location. They exist on
EnterpriseCompanyView scoped to this location via the locationId query param.
GalileoEvent CO
RE
FIELD TYPE DESCRIPTION
id stringUnique event ID — route /events/:id
enterpriseId stringOwning enterprise
name string Event display name
location string City or venue
startDate string (ISO 8601) Event start
endDate string (ISO 8601) Event end
attendees number Attendee count
service ServiceType Which agent types are running — "Hotel" | "Airline" | "Both"
status EventStatus Controls Active vs Completed placement and live ping dot
agents Agent[] All agents on this event
requirements string? Free-text requirements from ConfigureNegotiationPage
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 12/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
budgetPerPerson number? Per-person budget ceiling
Agent CO
RE
FIELD TYPE DESCRIPTION
id stringUnique agent ID — route /negotiations/:id/agent, SSE
stream U
R
Ls
enterpriseId stringOwning enterprise
eventId string Parent event
companyId string Target supplier — used to resolve linked-event relationship with
the company
companyName string Supplier display name — agent table rows,
NegotiationAgentPage title
segment string e.g. "Hospitality/Corporate" — agent table rows
type ServiceType "Hotel" | "Airline" — determines badge color and accept-offer
conflict logic
status AgentStatus Controls status chip color and CTA availability. "Negotiating" →
this event becomes a linked event for the company.
idealPrice number Anchor / target price — NegotiationAgentPage "Target Price"
card
ceilingPrice numberMax acceptable price guardrail
originalPrice number Pre-negotiation market baseline — EventDetailPage "Original
price", savings calc
currentPrice numberMost recent negotiated price — "Negotiation Price" column,
NegotiationAgentPage "Current Price" card
delta number % difference between idealPrice and currentPrice — Delta
column
potentialSavings number originalPrice − currentPrice × attendees — EventDetailPage
savingsToDate numberRealized savings — NegotiationAgentPage price path summary
distanceToGoal number currentPrice − idealPrice — NegotiationAgentPage price path
summary
isAccepted boolean Whether enterprise accepted this offer. true → this event
becomes a linked event for the company.
pricePath PricePoint[] Negotiation round history — price path charts
activityStream ActivityStreamItem[] Snapshot (augmented by SSE stream) — NegotiationAgentPage
timeline
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 13/17
4/4/26, 12:22 PM transcript Message[] Catapult Galileo — API Endpoints & Types
Snapshot (augmented by SSE stream) — transcript views
previousNegotiations PreviousNegotiation[] Past contracts with this supplier
Supporting Types
PricingTrend DERIVED One chart point on the pricing trends chart — computed from linked events
FIELD TYPE DESCRIPTION
month string "JAN" through "DEC" — X-axis label
year number Calendar year — distinguishes 1Y vs ALL data sets
range "1Y" | "ALL" Which toggle this point belongs to
negotiatedPrice number Avg rate from accepted/negotiating agents in this month — green solid line
marketPrice number Prevailing market average — gray dashed line
BookingWindowEntry DERIVED One month's booking quality — computed from linked event timing
history
FIELD TYPE DESCRIPTION
month string "JAN" through "DEC" — 4-col grid on booking window widget
score number (0–100) Composite deal-quality score — drives progress bar width and opacity
status BookingWindowStatus "Best Deal" | "Good" | "Peak" — label below the bar
PricePoint SUPPO
RTING One round in the negotiation price path chart
FIELD TYPE DESCRIPTION
label string "Anchor" | "Round 1" | "Counter" | "Current" etc.
price number Price in dollars at this round
type PricePointType offer = orange, negotiated = blue, current = green
round number Sequential round number — ordering
ActivityStreamItem STREAM
One entry in the live agent activity timeline
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 14/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
FIELD TYPE DESCRIPTION
id stringUnique item ID for deduplication
agentId string Parent agent reference
price number Price at this moment — main value in timeline row
badge string?Optional label e.g. "Saved 5.2%"
badgeType "savings" | "error" | "neutral" Controls badge color
detail string Description e.g. "−$15.00 from previous bid"
detailType "positive" | "negative" | "neutral"? Controls detail text color
timestamp string (ISO 8601)Rendered as relative string ("2m ago")
active boolean True = current item — green dot, f
ull opacity; others
dimmed
Message STREAM A single message in the negotiation transcript
FIELD TYPE DESCRIPTION
id stringUnique message ID
agentId string Parent agent reference
message string Full message body
sender "Galileo" | "Rep" Galileo = left with "G" avatar; Rep = right with person avatar
timestamp string (ISO 8601) Shown below each message bubble
PreviousNegotiation SUPPO
RTING A historical contract with a supplier
FIELD TYPE DESCRIPTION
id stringUnique negotiation record ID
contractId string Contract reference code e.g. "LH-2023-0492"
region string Geographic scope e.g. "EMEA Corporate"
duration string Contract length e.g. "24 Months"
finalRate number Achieved rate per night or seat
totalSavings number Total savings over contract duration
status "ACTIVE" | "ARCHIVED" Whether contract is still in force
startDate string (ISO 8601) Contract start
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 15/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
endDate string (ISO 8601) Contract end
Request & Response Types
MarketPricingResultRESPONSEResponse from POST /market/pricing
FIELD TYPE DESCRIPTION
service ServiceType Echoes the requested service type
hotel.marketPrice number? Current market rate per room night — NegotiationShellPage "Expected
Market Price"
hotel.predictedWinPrice number? Galileo's predicted negotiated price — NegotiationShellPage
"Predicted Win" (green)
hotel.unit string? "per night"
airline.marketPrice number? Current market rate per seat
airline.predictedWinPrice number? Galileo's predicted negotiated price per seat
airline.unit string? "per seat"
EventWindowResultRESPONSE One recommendation window
from POST /market/event-window
FIELD TYPE DESCRIPTION
label string "Best Overall" | "Backup Window" | "Budget Window"
startDate string (ISO 8601)Recommended start date
endDate string (ISO 8601)Recommended end date
explanation string AI-generated rationale — results card paragraph
hotel.marketCost number? Expected market rate per room night
hotel.negotiatedPrice number? Expected Galileo win price per room night
hotel.savings number? Per-room-night savings
airline.marketCost number? Expected market price per seat
airline.negotiatedPrice number? Expected Galileo win price per seat
airline.savings number? Per-seat savings
negotiationConfidence number (0–100) "94% negotiation confidence" pill on results card
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 16/17
4/4/26, 12:22 PM Catapult Galileo — API Endpoints & Types
AcceptOfferRequestREQUEST Body for POST /events/{eventId}/agents/{agentId}/accept
FIELD TYPE REQUIRED DESCRIPTION
enterpriseId string ✓ Used to update enterprise savings totals and establish the company linked-
event relationship
InterventionResultRESPONSEResponse from POST /agents/{agentId}/intervene
FIELD TYPE DESCRIPTION
agentId string Agent that was paused
status string "Routed" — confirms call transferred
callRoutingInfo string? Phone number or conference bridge for human to join
transferredAt string (ISO 8601) Timestamp of intervention
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/galileo-api-spec.html 17/17

4/4/26, 12:57 PM Catapult Galileo API Endpoints & Types v5
C ATA P U LT G A L I L E O
API Endpoints & Types v5
This revision documents the updated agent call status model and the additional company-page fields currently
available in app data but not consistently exposed in the interface.
LIFECYCLE STATES
6 statuses
NEGOTIATION OUTCOMES
6 outcomes
COMPANY METADATA
4 field groups
Status Model Change
Agent lifecycle and negotiation outcome are now treated as separate concepts. Lifecycle covers call progress. Outcome describes the
result once the call is completed.
Important acceptance rule
Event acceptance should only unlock when an agent is COMPLETED and the outcome is RATE_CONFIRMED, shown in UI as Deal
Closed.
Agent Lifecycle Status
INTERNAL ENUM UI LABEL MEANING UI TONE
INITIALIZING Queued Worker created before the outbound call is placed. Neutral
RINGING Ringing Twilio call placed and waiting for answer. Live
ACTIVE Negotiating Call connected and Galileo is actively negotiating. Live
WRAPPING_UP Finalizing Reserved for end-of-call wrap-up. Defined but not currently triggered. Neutral
COMPLETED Completed Call ended and post-call analysis completed normally. Success
FAILED Failed Twilio failure or unrecoverable call error. Error
Negotiation Outcomes
INTERNAL ENUM UI LABEL MEANING RESULT
RATE_CONFIRMED Deal Closed Supplier confirmed commercial terms and the deal can be
accepted.
Supplier asked Galileo to reconnect later. CLASS
Acceptable
Follow-up
CALLBACK_REQUESTED Callback
requested
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/Catapult%20Galileo%20Endpoints%20v5.html 1/2
4/4/26, 12:57 PM Catapult Galileo API Endpoints & Types v5
INTERNAL ENUM UI LABEL MEANING RESULT
NO_AVAILABILITY No Availability Requested room inventory was unavailable for the target dates. CLASS
Closed Out
ESCALATED_TO_HUMAN Moved to higher
Supplier transferred the call to a manager or more senior owner. Escalated
up
FAILED Failure Generic negotiation failure after the call completed. Error
TIMED_OUT Timed Out Call reached the maximum negotiation duration. Error
Company Page Data Not Fully Surfaced
The company experience already has additional data available in dashboard-data.ts that can be documented and exposed in future UI
revisions.
Directory Card Fields
totalSavings: aggregate savings shown on company
cards.
bookings: booking volume shown on company cards.
type: hotel or airline classification used for iconography.
initials: avatar token displayed in card and detail
views.
Supplier Profile Fields
displayName: richer supplier identity, for example Lumina
Hospitality Group.
description: long-form supplier summary used on the
detail page.
id: routing and lookup key for the company profile.
type: shared with icon treatment and page labeling.
Location Analytics Data
Per-location lifetime savings and delta values.
Scope-specific agreement counts and subtitles.
1Y and ALL pricing series for negotiated versus market
rates.
Event-to-location associations for contextual filtering.
Suggested Exposure
Show supplier profile metadata in the endpoint spec for
company detail responses.
Surface location analytics as an optional nested payload
for company detail endpoints.
Keep directory cards lightweight while documenting
extended detail fields separately.
Preserve current naming so the frontend can stay aligned
with the app model.
Prepared for the Catapult dashboard workspace. Version 5 reflects the updated agent status labels and the currently under-documented company-page
fields.
file:///Users/nathanielkemmenash/Desktop/Catapult26/catapultHacks26/dashboard/src/Catapult%20Galileo%20Endpoints%20v5.html 2/2