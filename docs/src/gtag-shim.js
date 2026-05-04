/*
 * Prevent runtime crashes when Google Analytics script is blocked by
 * privacy tools. Docusaurus route tracking may still call window.gtag.
 */

if (typeof window !== 'undefined') {
  window.dataLayer = window.dataLayer || [];

  if (typeof window.gtag !== 'function') {
    window.gtag = function gtag() {
      window.dataLayer.push(arguments);
    };
  }
}
