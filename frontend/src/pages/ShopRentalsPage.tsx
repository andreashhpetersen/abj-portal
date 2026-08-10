/**
 * Placeholder for feature 2: shop-rental applications.
 *
 * Only reachable by members of the erhvervsudvalg — the route is gated in
 * App.tsx and the API is gated by IsBusinessCommittee. Both checks matter: the
 * client-side one is convenience, the server-side one is the real boundary.
 */
export function ShopRentalsPage() {
  return (
    <section className="card">
      <h1>Erhvervslejemål</h1>
      <p>Ansøgninger vises her, når integrationen med Google Forms er bygget.</p>
    </section>
  )
}
