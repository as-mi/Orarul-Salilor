// Formularul unui eveniment (templates/admin_eveniment.html).

// Alegerea unei culori proprii o si bifeaza, iar mostra ia culoarea aleasa.
(() => {
  const optiune = document.getElementById("culoare-proprie");
  const culoare = optiune.querySelector('input[type="color"]');
  const aplica = () => {
    optiune.style.setProperty("--ev", culoare.value);
    optiune.querySelector('input[type="radio"]').checked = true;
  };
  culoare.addEventListener("input", aplica);
  culoare.addEventListener("click", aplica);
})();

// Ce tine de o alegere (salile, locul, specializarile) se vede doar cand e aleasa.
(() => {
  const formular = document.getElementById("formular-eveniment");
  const sincron = () => {
    for (const sub of formular.querySelectorAll(".sub-alegere")) {
      const grup = sub.closest("fieldset");
      const ales = grup.querySelector('input[type="radio"]:checked');
      const activ = ales && ales.value === sub.dataset.pentru;
      sub.hidden = !activ;
      for (const camp of sub.querySelectorAll("input")) camp.disabled = !activ;
    }
  };
  // "toate ..." bifeaza sau debifeaza tot grupul si tine pasul cu bifele lui
  const bifele = (grup) => [...grup.querySelectorAll('input[type="checkbox"]:not([data-toate])')];
  const tine_pasul = (grup) => {
    const toate = grup.querySelector("[data-toate]");
    const bifate = bifele(grup).filter((b) => b.checked).length;
    toate.checked = bifate > 0 && bifate === bifele(grup).length;
    toate.indeterminate = bifate > 0 && !toate.checked;
  };
  for (const grup of formular.querySelectorAll(".bife-grup")) {
    tine_pasul(grup);
    grup.addEventListener("change", (e) => {
      if (e.target.matches("[data-toate]")) bifele(grup).forEach((b) => { b.checked = e.target.checked; });
      tine_pasul(grup);
    });
  }
  formular.addEventListener("change", sincron);
  document.body.addEventListener("htmx:afterSwap", sincron);
  sincron();
})();
