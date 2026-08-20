import { bodyAnswers } from '../api/shoprentals'
import { formatDate } from '../lib/dates'
import { StarRating } from './StarRating'

import type { Application } from '../api/shoprentals'

interface Props {
  application: Application
  selected: boolean
  onSelect: () => void
}

/**
 * One row in the applications list.
 *
 * Deliberately thin: enough to decide whether to open it — who applied, when,
 * what state it is in, whether anyone owns it, and how much has been said about
 * it. The answers themselves are in the detail panel, because the whole point of
 * the list is being able to skim a long one.
 */
export function ApplicationCard({ application, selected, onSelect }: Props) {
  // The form is expected to change, so the summary line is simply the first
  // answer the header does not already show — whatever question that happens to
  // be. Better than naming a field that may not exist next year.
  const summary = bodyAnswers(application).find((answer) => answer.value)

  return (
    <button
      type="button"
      className={`application-row${selected ? ' application-row--selected' : ''}`}
      aria-current={selected}
      onClick={onSelect}
    >
      <div className="application-row__top">
        <span className="application-row__who">
          {application.applicant_name || application.email || 'Ukendt ansøger'}
        </span>
        <span className={`badge badge--${application.status}`}>{application.status_display}</span>
      </div>

      {summary && <p className="application-row__summary">{summary.value}</p>}

      <div className="application-row__meta">
        <span>{formatDate(application.submitted_at)}</span>
        <StarRating value={application.rating} />
        {application.assignee && <span>· {application.assignee.name}</span>}
        {application.comment_count > 0 && <span>· {application.comment_count} 💬</span>}
      </div>
    </button>
  )
}
