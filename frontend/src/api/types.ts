/**
 * Friendly aliases over the generated OpenAPI types.
 *
 * `generated/openapi.ts` is produced by `npm run gen:types` from ../contracts/openapi.json and
 * must not be edited by hand. Everything else in the app imports types from here (via
 * `@/api`), so a contract change surfaces as a type error in one place.
 */
import type { components } from './generated/openapi'

type Schemas = components['schemas']

// Core documents
export type TripPlan = Schemas['TripPlan']
export type TripContext = Schemas['TripContext']
export type SessionView = Schemas['SessionView']
export type ConversationTurn = Schemas['ConversationTurn']
export type PreferenceProfile = Schemas['PreferenceProfile']

// Turn result and streaming
export type TurnResult = Schemas['TurnResult']
export type TurnEvent = Schemas['TurnEvent']
export type TurnEventType = Schemas['TurnEventType']
export type TurnError = Schemas['TurnError']
export type TurnMetrics = Schemas['TurnMetrics']
export type Clarification = Schemas['Clarification']
export type QuickAnswer = Schemas['QuickAnswer']
export type GateResult = Schemas['GateResult']
export type GateReason = Schemas['GateReason']
export type Route = Schemas['Route']
export type AgentName = Schemas['AgentName']

// Plan building blocks
export type DayPlan = Schemas['DayPlan']
export type ItineraryItem = Schemas['ItineraryItem']
export type DailyForecast = Schemas['DailyForecast']
export type Place = Schemas['Place']
export type GeoPoint = Schemas['GeoPoint']
export type Hotel = Schemas['Hotel']
export type HotelStay = Schemas['HotelStay']
export type TicketOption = Schemas['TicketOption']
export type Reservation = Schemas['Reservation']
export type CostBreakdown = Schemas['CostBreakdown']
export type Money = Schemas['Money']
export type SectionState = Schemas['SectionState']
export type SectionStatus = Schemas['SectionStatus']
export type ConstraintViolation = Schemas['ConstraintViolation']
export type CheckName = Schemas['CheckName']
export type Disruption = Schemas['Disruption']
export type DisruptionKind = Schemas['DisruptionKind']
export type HardConstraint = Schemas['HardConstraint']
export type ConstraintKind = Schemas['ConstraintKind']
export type SourceRef = Schemas['SourceRef']

// Request / response bodies
export type ConfirmRequest = Schemas['ConfirmRequest']
export type FeedbackRequest = Schemas['FeedbackRequest']
export type FeedbackResponse = Schemas['FeedbackResponse']
export type HealthResponse = Schemas['HealthResponse']

// Plan workspace: chat focus and manual stop edits
export type PlanFocus = Schemas['PlanFocus']
export type FocusKind = Schemas['FocusKind']
export type PlanItemPatch = Schemas['PlanItemPatch']

/**
 * The generated ConfirmRequest marks `confirmed` as required because the schema declares a
 * default (true); callers may leave it out and the client fills the default in.
 */
export type ConfirmInput = Omit<ConfirmRequest, 'confirmed'> & { confirmed?: boolean }
