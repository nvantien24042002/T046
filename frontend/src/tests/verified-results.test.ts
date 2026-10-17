import { describe, expect, it, vi } from 'vitest'
import {
  createVerifiedResultsTransport,
  decodeVerifiedResultDetail,
  decodeVerifiedResultList,
  VerifiedResultError,
  type VerifiedResultDetail,
} from '../api/verifiedResults'
import { intermediaryOrigin } from '../api/analyticsV1/schemas'

const validDescriptor = {
  result_id: 'res-1',
  dataset_id: 'ds-1',
  archive_sha256: 'a'.repeat(64),
  config_id: 'cfg-1',
  request_fingerprint: null,
  configuration_schema_revision: 'config-v1',
  effective_config: {
    image_cfg: { blur_threshold: { kind: 'integer' as const, decimal: '100' } },
    label_cfg: { min_area_pct: { kind: 'binary64' as const, bits: '3f847ae147ae147b' } },
  },
  defaults_version: 'def-v1',
  canonicalization_version: 'canon-v1',
  algorithm_version: 'algo-v1',
  producer_build_id: 'b-1',
  payload_sha256: 'b'.repeat(64),
  verification: 'verified' as const,
}

const validPayload = {
  dataset_id: 'ds-1',
  quality_rows: [{
    filename: 'test.jpg',
    status: 'PASS' as const,
    score: 95,
    issues: [],
    width: 640,
    height: 480,
    size_kb: 50.5,
    blur_score: 120.0,
    brightness: 110.0,
    contrast: 40.0,
    noise_score: null,
    black_pct: 0.1,
    white_pct: 0.2,
    error: '',
  }],
  audit_rows: [{
    image_id: '1',
    image_name: 'test.jpg',
    box_id: 'box-1',
    label: 'car',
    occluded: '0',
    xtl: 10,
    ytl: 10,
    xbr: 100,
    ybr: 100,
    image_available: true,
    image_error: null,
    area_pct: 2.5,
    aspect_ratio: 1.0,
    edge_density: 0.2,
    texture_variance: 15.0,
    laplacian_variance: 120.0,
    foreground_contrast: 30.0,
    boundary_support: 0.5,
    geometry_issues: '',
    visual_issues: '',
    issues: '',
    issue_count: 0,
    priority: 'LOW' as const,
    status: 'NO_SIGNAL' as const,
  }],
  classes: [{ class: 'car', boxes: 1, images: 1, share_pct: 100.0 }],
  attribute_distribution: [],
  distribution_images: [],
  distribution_boxes: [],
  attributes: [],
}

const validEnvelope: VerifiedResultDetail = {
  schema_version: 'analytics.v1',
  descriptor: validDescriptor,
  provenance: {
    verification: 'verified',
    config_id: 'cfg-1',
    payload_sha256: 'b'.repeat(64),
  },
  payload: validPayload,
}

describe('verified results schema and runtime validation', () => {
  it('decodes a valid verified result list', () => {
    const list = decodeVerifiedResultList({
      schema_version: 'analytics.v1',
      dataset_id: 'ds-1',
      results: [validDescriptor],
    })
    expect(list.results.length).toBe(1)
    expect(list.results[0]!.result_id).toBe('res-1')
  })

  it('rejects malformed result list', () => {
    expect(() => decodeVerifiedResultList({ schema_version: 'invalid' })).toThrow(VerifiedResultError)
  })

  it('decodes a valid verified result detail envelope', () => {
    const detail = decodeVerifiedResultDetail(validEnvelope, 'ds-1', 'res-1')
    expect(detail.descriptor.result_id).toBe('res-1')
    expect(detail.payload.quality_rows.length).toBe(1)
  })

  it('rejects dataset mismatch in detail envelope', () => {
    expect(() => decodeVerifiedResultDetail(validEnvelope, 'other-ds', 'res-1')).toThrow(VerifiedResultError)
  })

  it('rejects result_id mismatch in detail envelope', () => {
    expect(() => decodeVerifiedResultDetail(validEnvelope, 'ds-1', 'other-res')).toThrow(VerifiedResultError)
  })

  it('rejects provenance config_id mismatch with descriptor', () => {
    const tampered = {
      ...validEnvelope,
      provenance: { ...validEnvelope.provenance, config_id: 'tampered-cfg' },
    }
    expect(() => decodeVerifiedResultDetail(tampered, 'ds-1', 'res-1')).toThrow(VerifiedResultError)
  })

  it('rejects provenance payload_sha256 mismatch with descriptor', () => {
    const tampered = {
      ...validEnvelope,
      provenance: { ...validEnvelope.provenance, payload_sha256: 'c'.repeat(64) },
    }
    expect(() => decodeVerifiedResultDetail(tampered, 'ds-1', 'res-1')).toThrow(VerifiedResultError)
  })
})

describe('verified results transport', () => {
  it('requests result list with Authorization header and handles response', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ schema_version: 'analytics.v1', dataset_id: 'ds-1', results: [validDescriptor] }), { status: 200 })
    )
    const transport = createVerifiedResultsTransport(intermediaryOrigin, fetcher)
    const list = await transport.listResults('ds-1', 'test-token')
    expect(fetcher).toHaveBeenCalledWith(
      intermediaryOrigin + '/analytics/v1/datasets/ds-1/results',
      expect.objectContaining({
        headers: { Accept: 'application/json', Authorization: 'Bearer test-token' },
      })
    )
    expect(list.results.length).toBe(1)
  })

  it('requests result detail with Authorization header and validates envelope', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify(validEnvelope), { status: 200 })
    )
    const transport = createVerifiedResultsTransport(intermediaryOrigin, fetcher)
    const detail = await transport.readResult('ds-1', 'res-1', 'test-token')
    expect(fetcher).toHaveBeenCalledWith(
      intermediaryOrigin + '/analytics/v1/datasets/ds-1/results/res-1',
      expect.objectContaining({
        headers: { Accept: 'application/json', Authorization: 'Bearer test-token' },
      })
    )
    expect(detail.descriptor.result_id).toBe('res-1')
  })

  it('maps HTTP errors with status and code', async () => {
    const fetcher = vi.fn<typeof fetch>().mockResolvedValue(
      new Response(JSON.stringify({ error: { code: 'DATASET_NOT_READY' } }), { status: 409 })
    )
    const transport = createVerifiedResultsTransport(intermediaryOrigin, fetcher)
    await expect(transport.listResults('ds-1', 'test-token')).rejects.toMatchObject({
      kind: 'http',
      status: 409,
      code: 'DATASET_NOT_READY',
    })
  })
})
