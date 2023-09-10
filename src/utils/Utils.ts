export const strip = (s: string, max: number) => {
  if (s.length > max) {
    return s.slice(0, max) + '...';
  }
  return s;
}
