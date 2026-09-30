// Comportamente marunte, comune intregului sit. Totul prin delegare pe `document`: merg si
// pe bucatile de pagina inlocuite de htmx, fara sa fie legate din nou.

// Meniul contului (bara de sus) se inchide la un click in afara lui sau la Escape.
document.addEventListener("click", (e) => {
  const meniu = document.querySelector(".meniu-cont[open]");
  if (meniu && !meniu.contains(e.target)) meniu.open = false;
});
document.addEventListener("keydown", (e) => {
  const meniu = document.querySelector(".meniu-cont[open]");
  if (meniu && e.key === "Escape") meniu.open = false;
});

// Panoul de admin: cautarea in tabelul de utilizatori, dupa nume sau email.
document.addEventListener("input", (e) => {
  if (e.target.id !== "cauta-utilizator") return;
  const q = e.target.value.trim().toLowerCase();
  for (const rand of document.querySelectorAll("#utilizatori tbody tr"))
    rand.hidden = q && !rand.dataset.cauta.includes(q);
});
