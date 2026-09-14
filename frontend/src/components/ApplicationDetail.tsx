/**
 * Everything about one application, in three parts: what the committee decides,
 * what the applicant wrote, and the contract preparation.
 *
 * The answers are rendered from whatever the server sends, in the form's own
 * order, with no field names hard-coded here. The form is expected to change,
 * and a UI that names its questions would need editing every time it does.
 */

import { useCallback, useEffect, useState } from 'react'

import { fieldErrors } from '../api/client'
import {
  STATUS_LABELS,
  STATUS_ORDER,
  bodyAnswers,
  shopRentals,
  splitQuestion,
} from '../api/shoprentals'
import { daysSince, formatDate } from '../lib/dates'
import { ContractDetailsForm } from './ContractDetailsForm'
import { StarRating } from './StarRating'

import type {
  Application,
  ApplicationComment,
  ApplicationStatus,
  CommitteeMember,
} from '../api/shoprentals'

interface Props {
  application: Application
  members: CommitteeMember[]
  /** Called with the updated application so the list can refresh in place. */
  onChanged: (updated: Application) => void
}

export function ApplicationDetail({ application, members, onChanged }: Props) {
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [comments, setComments] = useState<ApplicationComment[]>([])
  const [draft, setDraft] = useState('')
  const [posting, setPosting] = useState(false)
  const [tab, setTab] = useState<'answers' | 'contract'>('answers')

  const loadComments = useCallback(async () => {
    setComments(await shopRentals.comments(application.id))
  }, [application.id])

  useEffect(() => {
    let cancelled = false
    setComments([])
    setDraft('')
    setError(null)

    async function load() {
      try {
        const loaded = await shopRentals.comments(application.id)
        if (!cancelled) setComments(loaded)
      } catch {
        if (!cancelled) setError('Kommentarerne kunne ikke hentes.')
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [application.id])

  async function patch(changes: Parameters<typeof shopRentals.update>[1]) {
    setBusy(true)
    setError(null)
    try {
      onChanged(await shopRentals.update(application.id, changes))
    } catch (caught) {
      const fields = fieldErrors(caught)
      setError(Object.values(fields)[0] ?? 'Ændringen kunne ikke gemmes.')
    } finally {
      setBusy(false)
    }
  }

  async function addComment() {
    if (!draft.trim()) return
    setPosting(true)
    setError(null)
    try {
      await shopRentals.addComment(application.id, draft.trim())
      setDraft('')
      await loadComments()
      // The comment count lives on the application, so the list row needs the
      // fresh copy rather than a locally incremented guess.
      onChanged(await shopRentals.get(application.id))
    } catch {
      setError('Kommentaren kunne ikke gemmes.')
    } finally {
      setPosting(false)
    }
  }

  async function removeComment(id: number) {
    setError(null)
    try {
      await shopRentals.deleteComment(id)
      await loadComments()
      onChanged(await shopRentals.get(application.id))
    } catch {
      setError('Kommentaren kunne ikke slettes.')
    }
  }

  const standing =
    application.status_changed_at === null ? null : daysSince(application.status_changed_at)
  // The header already carries name, email and phone, so the answers list shows
  // what is left rather than repeating them.
  const answers = bodyAnswers(application)

  return (
    <article className="application card">
      <header className="application__header">
        <div>
          <h2 className="application__who">
            {application.applicant_name || application.email || 'Ukendt ansøger'}
          </h2>
          <p className="application__contact">
            {application.email && <a href={`mailto:${application.email}`}>{application.email}</a>}
            {application.phone && (
              <>
                {application.email && ' · '}
                <a href={`tel:${application.phone.replace(/\s/g, '')}`}>{application.phone}</a>
              </>
            )}
          </p>
          <p className="application__submitted">
            Modtaget {formatDate(application.submitted_at)}
          </p>
        </div>
        <StarRating
          value={application.rating}
          disabled={busy}
          onChange={(rating) => void patch({ rating })}
        />
      </header>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="application__controls">
        <label>
          Status
          <select
            value={application.status}
            disabled={busy}
            onChange={(event) => void patch({ status: event.target.value as ApplicationStatus })}
          >
            {STATUS_ORDER.map((status) => (
              <option key={status} value={status}>
                {STATUS_LABELS[status]}
              </option>
            ))}
          </select>
        </label>

        <label>
          Ansvarlig
          <select
            value={application.assignee?.id ?? ''}
            disabled={busy}
            onChange={(event) =>
              void patch({
                assignee_id: event.target.value === '' ? null : Number(event.target.value),
              })
            }
          >
            <option value="">Ingen</option>
            {members.map((member) => (
              <option key={member.id} value={member.id}>
                {member.name}
              </option>
            ))}
          </select>
        </label>
      </div>

      {/* The committee's own complaint is that "i gang" drags on for months, so
          say how long it has been sitting rather than only which pile it is in. */}
      {standing !== null && standing > 0 && (
        <p className="hint">
          {STATUS_LABELS[application.status].toLowerCase()} i {standing}{' '}
          {standing === 1 ? 'dag' : 'dage'}
        </p>
      )}

      <nav className="tabs">
        <button
          type="button"
          className={tab === 'answers' ? 'tab tab--on' : 'tab'}
          onClick={() => setTab('answers')}
        >
          Ansøgningen
        </button>
        <button
          type="button"
          className={tab === 'contract' ? 'tab tab--on' : 'tab'}
          onClick={() => setTab('contract')}
        >
          Kontraktoplysninger
        </button>
      </nav>

      {tab === 'answers' ? (
        <dl className="answers">
          {answers.length === 0 && <p className="hint">Ansøgningen indeholder ingen svar.</p>}
          {answers.map((answer, index) => {
            const { title, description } = splitQuestion(answer.question)
            return (
              // Indexed: the form may ask two questions with the same wording,
              // and the position is what makes them distinct.
              <div className="answers__item" key={`${answer.question}-${index}`}>
                <dt>
                  {title}
                  {description && <span className="answers__note">{description}</span>}
                </dt>
                <dd>{answer.value || <span className="hint">(ikke besvaret)</span>}</dd>
              </div>
            )
          })}
        </dl>
      ) : (
        <ContractDetailsForm
          applicationId={application.id}
          onSaved={() => void shopRentals.get(application.id).then(onChanged)}
        />
      )}

      <section className="comments">
        <h3>Kommentarer</h3>
        {comments.length === 0 && <p className="hint">Ingen kommentarer endnu.</p>}
        {comments.map((comment) => (
          <div className="comment" key={comment.id}>
            <div className="comment__meta">
              {comment.author.name || comment.author.email} · {formatDate(comment.created_at)}
              {comment.can_delete && (
                <button
                  type="button"
                  className="comment__delete"
                  onClick={() => {
                    if (window.confirm('Slet denne kommentar?')) {
                      void removeComment(comment.id)
                    }
                  }}
                >
                  Slet
                </button>
              )}
            </div>
            <p className="comment__body">{comment.body}</p>
          </div>
        ))}

        <textarea
          rows={2}
          value={draft}
          placeholder="Skriv en kommentar…"
          onChange={(event) => setDraft(event.target.value)}
        />
        <button type="button" disabled={posting || !draft.trim()} onClick={() => void addComment()}>
          {posting ? 'Gemmer…' : 'Tilføj kommentar'}
        </button>
      </section>
    </article>
  )
}
