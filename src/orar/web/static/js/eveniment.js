// Formularul unui eveniment (templates/_eveniment_formular_panou.html / admin_eveniment.html).
(() => {
  // Formularul poate fi incarcat prin htmx (in panoul de admin) de mai multe ori; ascultatorii
  // sunt delegati la document, deci raman valabili dupa inlocuirea formularului.
  if (window.__evenimentJs) return;
  window.__evenimentJs = true;

  const culoare_proprie = (e) => {
    const culoare = e.target;
    if (!culoare.matches('input[type="color"]')) return;
    const optiune = culoare.closest("#culoare-proprie");
    optiune.style.setProperty("--ev", culoare.value);
    optiune.querySelector('input[type="radio"]').checked = true;
  };
  document.addEventListener("input", culoare_proprie);
  document.addEventListener("click", culoare_proprie);

  const sincron = (formular) => {
    // Ce tine de o alegere (salile, locul, specializarile) se vede doar cand e aleasa.
    for (const sub of formular.querySelectorAll(".sub-alegere")) {
      const grup = sub.closest("fieldset");
      const ales = grup.querySelector('input[type="radio"]:checked');
      const activ = ales && ales.value === sub.dataset.pentru;
      sub.hidden = !activ;
      for (const camp of sub.querySelectorAll("input")) camp.disabled = !activ;
    }
    // "toate ..." tine pasul cu bifele grupului.
    for (const grup of formular.querySelectorAll(".bife-grup")) {
      const toate = grup.querySelector("[data-toate]");
      if (!toate) continue;
      const bifele = [...grup.querySelectorAll('input[type="checkbox"]:not([data-toate])')];
      const bifate = bifele.filter((b) => b.checked).length;
      toate.checked = bifate > 0 && bifate === bifele.length;
      toate.indeterminate = bifate > 0 && !toate.checked;
    }
  };

  document.addEventListener("change", (e) => {
    const formular = e.target.closest("#formular-eveniment");
    if (!formular) return;
    if (e.target.matches("[data-toate]")) {
      const grup = e.target.closest(".bife-grup");
      for (const b of grup.querySelectorAll('input[type="checkbox"]:not([data-toate])')) {
        b.checked = e.target.checked;
      }
    }
    sincron(formular);
  });

  document.addEventListener("htmx:afterSwap", () => {
    const formular = document.getElementById("formular-eveniment");
    if (formular) sincron(formular);
  });
  const gata = () => {
    const formular = document.getElementById("formular-eveniment");
    if (formular) sincron(formular);
  };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", gata);
  else gata();
})();
