const licenceLinks: Record<string, string> = {
  "CC BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
  "CC-BY-NC-SA 4.0": "https://creativecommons.org/licenses/by-nc-sa/4.0/",
  "CC BY-NC-ND 4.0": "https://creativecommons.org/licenses/by-nc-nd/4.0/",
  "CC BY 4.0": "https://creativecommons.org/licenses/by/4.0/",
  CC0: "https://creativecommons.org/publicdomain/zero/1.0/",
  "Open Government Licence v3.0":
    "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/",
}

export function Licence({ value }: { value: string }) {
  if (!value) return <>Not supplied</>
  return value.split(";").map((part, index) => {
    const name = part.trim()
    return (
      <span key={part}>
        {index > 0 && "; "}
        {licenceLinks[name] ? (
          <a href={licenceLinks[name]} target="_blank" rel="noreferrer">
            {name}
          </a>
        ) : (
          name
        )}
      </span>
    )
  })
}
