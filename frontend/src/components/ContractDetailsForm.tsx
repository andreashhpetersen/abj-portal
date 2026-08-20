/**
 * The facts gathered after making contact — the raw material for the document
 * the committee hands to the association's lawyer.
 *
 * Nothing here is required, because nothing here arrives at once: CVR, purpose,
 * area and rent turn up over weeks of correspondence. So the form saves field by
 * field and shows what is still outstanding rather than refusing to save until
 * it is whole. The server owns that checklist (`missing_fields`), so the UI does
 * not keep a second, drifting copy of what the lawyer needs.
 */

import { useEffect, useState } from 'react'

import { fieldErrors } from '../api/bookings'
import { shopRentals } from '../api/shoprentals'
import { formatDate } from '../lib/dates'

import type { ContractDetails } from '../api/shoprentals'

interface Props {
  applicationId: number
  /** Told to the parent so the list row's "details started" marker keeps up. */
  onSaved?: () => void
}

type Editable = Omit<ContractDetails, 'updated_at' | 'missing_fields' | 'is_complete'>

const TEXT_FIELDS: [keyof Editable, string, string?][] = [
  ['company_name', 'Virksomhedsnavn'],
  ['cvr', 'CVR-nummer'],
  ['legal_form', 'Selskabsform', 'F.eks. ApS, A/S, enkeltmandsvirksomhed'],
  ['contact_name', 'Kontaktperson'],
  ['contact_email', 'Kontaktemail'],
  ['contact_phone', 'Kontakttelefon'],
  ['unit_label', 'Lejemål', "F.eks. 'Jægergade 4, kld.'"],
]

const NUMBER_FIELDS: [keyof Editable, string][] = [
  ['area_sqm', 'Areal (m²)'],
  ['annual_rent_dkk', 'Årlig leje (kr.)'],
  ['deposit_months', 'Depositum (måneder)'],
]

export function ContractDetailsForm({ applicationId, onSaved }: Props) {
  const [details, setDetails] = useState<ContractDetails | null>(null)
  const [draft, setDraft] = useState<Partial<Editable>>({})
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [savedAt, setSavedAt] = useState<number | null>(null)

  useEffect(() => {
    let cancelled = false
    setDetails(null)
    setDraft({})
    setErrors({})
    setSavedAt(null)

    async function load() {
      try {
        const loaded = await shopRentals.details(applicationId)
        if (!cancelled) setDetails(loaded)
      } catch {
        if (!cancelled) setError('Kontraktoplysningerne kunne ikke hentes.')
      }
    }

    void load()
    return () => {
      cancelled = true
    }
  }, [applicationId])

  function edit(field: keyof Editable, value: string) {
    setDraft((current) => ({ ...current, [field]: value }))
  }

  function valueOf(field: keyof Editable): string {
    if (field in draft) return String(draft[field] ?? '')
    const stored = details?.[field]
    return stored === null || stored === undefined ? '' : String(stored)
  }

  async function save() {
    if (!details) return
    setSaving(true)
    setError(null)
    setErrors({})
    try {
      // Empty numeric inputs must go as null, not "": DRF would refuse "" for a
      // decimal, and clearing a rent that was entered by mistake has to work.
      const payload: Record<string, unknown> = {}
      for (const [field, value] of Object.entries(draft)) {
        const isNumeric = NUMBER_FIELDS.some(([name]) => name === field) || field === 'lease_start'
        payload[field] = isNumeric && value === '' ? null : value
      }
      const saved = await shopRentals.saveDetails(applicationId, payload)
      setDetails(saved)
      setDraft({})
      setSavedAt(Date.now())
      onSaved?.()
    } catch (caught) {
      const fields = fieldErrors(caught)
      if (Object.keys(fields).length > 0) {
        setErrors(fields)
      } else {
        setError('Kunne ikke gemme. Prøv igen.')
      }
    } finally {
      setSaving(false)
    }
  }

  if (!details) {
    return <p className="status">{error ?? 'Indlæser…'}</p>
  }

  const dirty = Object.keys(draft).length > 0

  return (
    <div className="contract">
      {details.is_complete ? (
        <p className="contract__ready">
          Alt til kontrakten er udfyldt. Klar til at sende til advokaten.
        </p>
      ) : (
        <p className="hint">
          Mangler stadig: {details.missing_fields.join(', ')}.
        </p>
      )}

      <div className="contract__grid">
        {TEXT_FIELDS.map(([field, label, hint]) => (
          <label key={field}>
            {label}
            <input
              type="text"
              value={valueOf(field)}
              placeholder={hint ?? ''}
              onChange={(event) => edit(field, event.target.value)}
            />
            {errors[field] && <span className="error">{errors[field]}</span>}
          </label>
        ))}

        {NUMBER_FIELDS.map(([field, label]) => (
          <label key={field}>
            {label}
            <input
              type="number"
              min="0"
              step={field === 'deposit_months' ? '1' : '0.01'}
              value={valueOf(field)}
              onChange={(event) => edit(field, event.target.value)}
            />
            {errors[field] && <span className="error">{errors[field]}</span>}
          </label>
        ))}

        <label>
          Ønsket overtagelse
          <input
            type="date"
            value={valueOf('lease_start')}
            onChange={(event) => edit('lease_start', event.target.value)}
          />
          {/* The native control shows the *browser's* date order, so a Danish
              user on an English browser is offered mm/dd/yyyy and the document's
              lang attribute cannot override it. The stored value is always ISO,
              so nothing is corrupted — but this date ends up in a lease, so echo
              it back in Danish where a misread is immediately visible. */}
          {valueOf('lease_start') && (
            <span className="hint">{formatDate(valueOf('lease_start'))}</span>
          )}
          {errors.lease_start && <span className="error">{errors.lease_start}</span>}
        </label>
      </div>

      <label>
        Anvendelse
        <textarea
          rows={2}
          value={valueOf('purpose')}
          placeholder="Hvad lejemålet må bruges til. Står i kontrakten, så vær konkret."
          onChange={(event) => edit('purpose', event.target.value)}
        />
      </label>

      <label>
        Virksomhedens adresse
        <textarea
          rows={2}
          value={valueOf('company_address')}
          onChange={(event) => edit('company_address', event.target.value)}
        />
      </label>

      <label>
        Noter til advokaten
        <textarea
          rows={3}
          value={valueOf('notes')}
          placeholder="Aftaler og forbehold, der ikke passer i felterne ovenfor."
          onChange={(event) => edit('notes', event.target.value)}
        />
      </label>

      {error && (
        <p role="alert" className="error">
          {error}
        </p>
      )}

      <div className="contract__actions">
        <button type="button" disabled={!dirty || saving} onClick={() => void save()}>
          {saving ? 'Gemmer…' : 'Gem oplysninger'}
        </button>
        {savedAt !== null && !dirty && <span className="hint">Gemt.</span>}
      </div>
    </div>
  )
}
