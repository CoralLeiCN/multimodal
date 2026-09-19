import { expect, test } from "@playwright/test"

for (const width of [1280, 390]) {
  test(`creates and versions a six-field brand without generation controls at ${width}px`, async ({ page }) => {
    await page.setViewportSize({width,height:900})
    let authenticated = false
    let profiles: Record<string, unknown>[] = []
    const submissions: Record<string, unknown>[] = []
    const mutations: string[] = []
    page.on('request',r => { if (r.method()==='POST') mutations.push(r.url()) })
    await page.route('**/api/v1/agent/status',r=>r.fulfill({json:{enabled:true,authenticated,ready:false,message:'Generation setup incomplete'}}))
    await page.route('**/api/v1/agent/session',r=>{authenticated=true;return r.fulfill({json:{authenticated}})})
    await page.route('**/api/v1/agent/brands',r=>{
      if(r.request().method()==='POST') {
        const body=r.request().postDataJSON();submissions.push(body)
        profiles=[{...body,id:'v1',brand_id:'brand',version:1}]
        return r.fulfill({status:201,json:profiles[0]})
      }
      return r.fulfill({json:profiles})
    })
    await page.route('**/api/v1/agent/brands/brand/versions',r=>{
      const body=r.request().postDataJSON();submissions.push(body)
      const updated={...body,id:'v2',brand_id:'brand',version:2};profiles=[updated,...profiles]
      return r.fulfill({status:201,json:updated})
    })
    await page.goto('/create')
    await expect(page.getByRole('heading',{name:'Create a brand',exact:true})).toBeVisible()
    await page.getByLabel('Workspace access key').fill('test-key')
    await page.getByRole('button',{name:'Sign in',exact:true}).click()
    const values={name:'Fieldwork',description:'Tools for curious designers',colors:'Blue #2255CC, ivory',personality:'premium, calm, technical, optimistic',typography:'Humanist sans serif',illustration_style:'Geometric shapes and fine outlines'}
    const labels=['Brand name','Brand description','Brand colors palette','Personality','Typography','Illustration style']
    for (let i=0;i<labels.length;i++) await page.getByLabel(labels[i],{exact:true}).fill(Object.values(values)[i])
    await page.getByRole('button',{name:'Save brand',exact:true}).click()
    await expect(page.getByRole('status')).toContainText('Brand saved')
    expect(submissions[0]).toEqual(values)
    await page.getByLabel('Saved brands').selectOption('')
    await page.getByLabel('Saved brands').selectOption('v1')
    await expect(page.getByLabel('Typography',{exact:true})).toHaveValue(values.typography)
    await page.getByLabel('Personality',{exact:true}).fill('calm and optimistic')
    await page.getByRole('button',{name:'Save brand changes'}).click()
    await expect(page.getByRole('status')).toContainText('Brand saved')
    expect(submissions[1].personality).toBe('calm and optimistic')
    await expect(page.getByRole('button',{name:'Generate images'})).toHaveCount(0)
    await expect(page.getByLabel('Elements to preserve')).toHaveCount(0)
    await expect(page.locator('input[type=file]')).toHaveCount(0)
    expect(mutations.every(url=>!url.includes('/runs')&&!url.includes('/assets'))).toBe(true)
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true)
    await page.screenshot({path:`/tmp/brand-form-${width}.png`,fullPage:true})
  })
}

test('loads an older brand and keeps edits when saving fails',async({page})=>{
  await page.route('**/api/v1/agent/status',r=>r.fulfill({json:{enabled:true,authenticated:true,ready:true}}))
  await page.route('**/api/v1/agent/brands',r=>r.fulfill({json:[{id:'old',brand_id:'brand',version:1,name:'Legacy',description:'Existing description',colors:'Green'}]}))
  await page.route('**/api/v1/agent/brands/brand/versions',r=>r.fulfill({status:503,json:{message:'Storage unavailable'}}))
  await page.goto('/create')
  await page.getByLabel('Saved brands').selectOption('old')
  await expect(page.getByLabel('Personality',{exact:true})).toHaveValue('')
  await page.getByLabel('Illustration style',{exact:true}).fill('Hand-drawn lines')
  await page.getByRole('button',{name:'Save brand changes'}).click()
  await expect(page.getByRole('alert')).toContainText('Storage unavailable')
  await expect(page.getByLabel('Illustration style',{exact:true})).toHaveValue('Hand-drawn lines')
})
