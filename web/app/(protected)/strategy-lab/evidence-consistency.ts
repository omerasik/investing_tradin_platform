import type { StrategyLabEvidenceIdentity } from "../../lib/data-access";

/**
 * Stage B.1.2b evidence binding. A visualization is shown only when the evidence it was
 * computed from is the study on screen, and the heatmap and the break-even distribution are
 * shown only when both were computed from the same stored result set. A mismatch withholds
 * the visualization (fail closed); it is never rendered next to a warning.
 */

/** The study as the page displays it; fields the detail read could not supply are skipped. */
export type StudyOnScreen = {
  studyId: string;
  contentHash?: string;
  /** Latest recorded manifest (``null`` = none recorded); ``undefined`` when the detail is unavailable. */
  latestManifestHash?: string | null;
  resultCount?: number;
};

export function evidenceMismatch(evidence: StrategyLabEvidenceIdentity, study: StudyOnScreen): string | null {
  if (evidence.study_id !== study.studyId) return "the response is for a different study";
  if (study.contentHash !== undefined && evidence.study_content_hash !== study.contentHash) {
    return "the study content hash differs from the study shown";
  }
  if (study.latestManifestHash !== undefined && evidence.manifest_hash !== study.latestManifestHash) {
    return "the latest manifest differs from the study shown";
  }
  if (study.resultCount !== undefined && evidence.result_count !== study.resultCount) {
    return "the stored result count differs from the study shown";
  }
  return null;
}

/** Both visualizations must come from one stored result set; with only one read there is nothing to compare. */
export function crossViewMismatch(
  heatmap: StrategyLabEvidenceIdentity | undefined, distribution: StrategyLabEvidenceIdentity | undefined,
): string | null {
  if (!heatmap || !distribution) return null;
  if (heatmap.study_id !== distribution.study_id || heatmap.study_content_hash !== distribution.study_content_hash) {
    return "the heatmap and the distribution were computed for different studies";
  }
  if (heatmap.manifest_hash !== distribution.manifest_hash || heatmap.results_digest !== distribution.results_digest) {
    return "the stored results changed between the heatmap and the distribution reads (digests differ)";
  }
  return null;
}
