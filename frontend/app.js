const API_URL =
    "http://localhost:8080/copy";

async function copyLineItem() {

    const sourceId =
        document.getElementById(
            "source"
        ).value;

    const targetId =
        document.getElementById(
            "target"
        ).value;

    if (!sourceId || !targetId) {

        alert(
            "Informe os dois IDs."
        );

        return;
    }

    try {

        const response =
            await fetch(
                API_URL,
                {
                    method: "POST",

                    headers: {
                        "Content-Type":
                            "application/json"
                    },

                    body: JSON.stringify({

                        source_id:
                            parseInt(sourceId),

                        target_id:
                            parseInt(targetId)

                    })
                }
            );

        const result =
            await response.json();

        document
            .getElementById("msg")
            .innerHTML =
            result.message;

    } catch (error) {

        document
            .getElementById("msg")
            .innerHTML =
            "Erro: " + error.message;
    }
}