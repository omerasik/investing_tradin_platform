import React from "react";
import { utc } from "../lib/data-access";

export function PITTimestampGroup({
  eventTime,
  effectiveTime,
  knowledgeTime,
  computedTime,
}: {
  eventTime: string | null | undefined;
  effectiveTime?: string | null | undefined;
  knowledgeTime?: string | null | undefined;
  computedTime?: string | null | undefined;
}) {
  return (
    <div className="pit-timestamp-group" aria-label="Point-in-Time Timestamps">
      <div className="pit-item">
        <span className="pit-label">EVENT</span>
        <time className="pit-time" dateTime={eventTime ?? undefined}>
          {utc(eventTime)}
        </time>
      </div>

      {effectiveTime && (
        <div className="pit-item">
          <span className="pit-label">EFFECTIVE</span>
          <time className="pit-time" dateTime={effectiveTime}>
            {utc(effectiveTime)}
          </time>
        </div>
      )}

      {knowledgeTime && (
        <div className="pit-item">
          <span className="pit-label">KNOWLEDGE</span>
          <time className="pit-time" dateTime={knowledgeTime}>
            {utc(knowledgeTime)}
          </time>
        </div>
      )}

      {computedTime && (
        <div className="pit-item">
          <span className="pit-label">COMPUTED</span>
          <time className="pit-time" dateTime={computedTime}>
            {utc(computedTime)}
          </time>
        </div>
      )}
    </div>
  );
}
