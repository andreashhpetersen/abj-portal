interface Props {
  /** 1–5, or null for not yet rated. */
  value: number | null
  /** Omit to render a read-only rating, as the list rows do. */
  onChange?: (rating: number | null) => void
  disabled?: boolean
}

const STARS = [1, 2, 3, 4, 5]

/**
 * A 1–5 rating.
 *
 * Clicking the star an application already has clears the rating, because
 * "not yet judged" is a real state and not the same as one star — the committee
 * needs to be able to take a snap judgement back.
 */
export function StarRating({ value, onChange, disabled = false }: Props) {
  if (!onChange) {
    return (
      <span className="stars stars--static" aria-label={value ? `${value} af 5` : 'Ikke vurderet'}>
        {STARS.map((star) => (
          <span key={star} className={star <= (value ?? 0) ? 'star star--on' : 'star'}>
            ★
          </span>
        ))}
      </span>
    )
  }

  return (
    <span className="stars" role="group" aria-label="Vurdering">
      {STARS.map((star) => (
        <button
          key={star}
          type="button"
          className={star <= (value ?? 0) ? 'star star--on' : 'star'}
          disabled={disabled}
          aria-pressed={star === value}
          // The label says what clicking does, which for the current rating is
          // to clear it — a screen reader should not have to guess.
          aria-label={star === value ? 'Fjern vurdering' : `Giv ${star} af 5`}
          title={star === value ? 'Fjern vurdering' : `${star} af 5`}
          onClick={() => onChange(star === value ? null : star)}
        >
          ★
        </button>
      ))}
    </span>
  )
}
