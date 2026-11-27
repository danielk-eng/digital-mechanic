// printable section page (backup stand without PDFs): «Сохранить в PDF» opens the print dialog, cited page scrolled into view
document.getElementById("pr")?.addEventListener("click", () => window.print());
const on = document.querySelector("h2.on"); if (on) on.scrollIntoView({block: "start"});
