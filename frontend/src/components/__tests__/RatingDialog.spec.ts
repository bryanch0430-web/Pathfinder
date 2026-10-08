import { beforeEach, describe, expect, it, vi } from 'vitest'
import { flushPromises, mount } from '@vue/test-utils'
import { createPinia, setActivePinia } from 'pinia'
import RatingDialog from '../RatingDialog.vue'
import { sendFeedback } from '@/api'
import { usePlanStore } from '@/stores/plan'
import { useSessionStore } from '@/stores/session'
import { useUiStore } from '@/stores/ui'
import { makePlan } from '@/test/fixtures'

vi.mock('@/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/api')>()),
  sendFeedback: vi.fn(),
}))

function mountDialog() {
  return mount(RatingDialog, { global: { stubs: { teleport: true } } })
}

describe('RatingDialog', () => {
  beforeEach(() => {
    setActivePinia(createPinia())
    vi.mocked(sendFeedback).mockReset().mockResolvedValue({ queued: true })
    usePlanStore().setPlan(makePlan())
    useSessionStore().sessionId = 'session-123'
  })

  it('is hidden until opened', () => {
    const wrapper = mountDialog()
    expect(wrapper.find('[role="dialog"]').exists()).toBe(false)
  })

  it('posts useful + the chosen star rating to the feedback endpoint', async () => {
    const ui = useUiStore()
    const wrapper = mountDialog()
    ui.openDialog('rating')
    await flushPromises()

    await wrapper.findAll('input[type="radio"]')[3]!.setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sendFeedback).toHaveBeenCalledTimes(1)
    expect(sendFeedback).toHaveBeenCalledWith('session-123', { useful: true, rating: 4 })
    expect(ui.dialog).toBeNull()
    expect(useSessionStore().feedbackOutcome).toMatchObject({ planId: 'plan-1', rating: 4, queued: true })
  })

  it('asks for a rating before saving', async () => {
    const ui = useUiStore()
    const wrapper = mountDialog()
    ui.openDialog('rating')
    await flushPromises()

    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(sendFeedback).not.toHaveBeenCalled()
    expect(wrapper.text()).toContain('Choose a rating from 1 to 5 stars.')
    expect(ui.dialog).toBe('rating')
  })

  it('stays open and shows the error when saving fails', async () => {
    const { ApiError } = await import('@/api')
    vi.mocked(sendFeedback).mockRejectedValue(new ApiError(409, 'No plan to rate yet'))
    const ui = useUiStore()
    const wrapper = mountDialog()
    ui.openDialog('rating')
    await flushPromises()

    await wrapper.findAll('input[type="radio"]')[0]!.setValue(true)
    await wrapper.get('form').trigger('submit')
    await flushPromises()

    expect(ui.dialog).toBe('rating')
    expect(wrapper.text()).toContain('No plan to rate yet')
  })
})
