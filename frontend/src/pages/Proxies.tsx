import { useEffect, useState } from 'react'
import { Card, Table, Button, Input, Tag, Space, Popconfirm, Typography, message } from 'antd'
import {
  PlusOutlined,
  DeleteOutlined,
  ReloadOutlined,
  CheckCircleOutlined,
  CloseCircleOutlined,
  SwapRightOutlined,
  SwapLeftOutlined,
  ApiOutlined,
  SaveOutlined,
} from '@ant-design/icons'
import { apiFetch } from '@/lib/utils'

export default function Proxies() {
  const [proxies, setProxies] = useState<any[]>([])
  const [newProxy, setNewProxy] = useState('')
  const [region, setRegion] = useState('')
  const [proxyApiUrl, setProxyApiUrl] = useState('')
  const [externalProxy, setExternalProxy] = useState('')
  const [checking, setChecking] = useState(false)
  const [loading, setLoading] = useState(false)
  const [savingApi, setSavingApi] = useState(false)
  const [fetchingExternal, setFetchingExternal] = useState(false)

  const load = async () => {
    setLoading(true)
    try {
      const data = await apiFetch('/proxies')
      setProxies(data)
    } finally {
      setLoading(false)
    }
  }

  const loadConfig = async () => {
    const data = await apiFetch('/config')
    setProxyApiUrl(data.proxy_api_url || '')
  }

  useEffect(() => {
    load()
    loadConfig()
  }, [])

  const add = async () => {
    if (!newProxy.trim()) return
    const lines = newProxy.trim().split('\n').map((l) => l.trim()).filter(Boolean)
    try {
      if (lines.length > 1) {
        await apiFetch('/proxies/bulk', {
          method: 'POST',
          body: JSON.stringify({ proxies: lines, region }),
        })
      } else {
        await apiFetch('/proxies', {
          method: 'POST',
          body: JSON.stringify({ url: lines[0], region }),
        })
      }
      message.success('添加成功')
      setNewProxy('')
      setRegion('')
      load()
    } catch (e: any) {
      message.error(`添加失败: ${e.message}`)
    }
  }

  const del = async (id: number) => {
    try {
      await apiFetch(`/proxies/${id}`, { method: 'DELETE' })
      message.success('删除成功')
      load()
    } catch (e: any) {
      message.error(`删除失败: ${e.message}`)
    }
  }

  const toggle = async (id: number) => {
    try {
      await apiFetch(`/proxies/${id}/toggle`, { method: 'PATCH' })
      load()
    } catch (e: any) {
      message.error(`切换失败: ${e.message}`)
    }
  }

  const check = async () => {
    try {
      setChecking(true)
      await apiFetch('/proxies/check', { method: 'POST' })
      setTimeout(() => {
        load()
        setChecking(false)
      }, 3000)
    } catch (e: any) {
      setChecking(false)
      message.error(`检测失败: ${e.message}`)
    }
  }

  const saveProxyApi = async () => {
    try {
      setSavingApi(true)
      await apiFetch('/config', {
        method: 'PUT',
        body: JSON.stringify({ data: { proxy_api_url: proxyApiUrl.trim() } }),
      })
      message.success('外部代理接口已保存')
    } catch (e: any) {
      message.error(`保存失败: ${e.message}`)
    } finally {
      setSavingApi(false)
    }
  }

  const fetchExternalProxy = async () => {
    try {
      setFetchingExternal(true)
      const data = await apiFetch('/proxies/fetch-external', {
        method: 'POST',
        body: JSON.stringify({ api_url: proxyApiUrl.trim() || null }),
      })
      setExternalProxy(data.proxy || '')
      message.success('获取成功')
    } catch (e: any) {
      setExternalProxy('')
      message.error(`获取失败: ${e.message}`)
    } finally {
      setFetchingExternal(false)
    }
  }

  const columns: any[] = [
    {
      title: '代理地址',
      dataIndex: 'url',
      key: 'url',
      render: (text: string) => <span style={{ fontFamily: 'monospace', fontSize: 12 }}>{text}</span>,
    },
    {
      title: '地区',
      dataIndex: 'region',
      key: 'region',
      render: (text: string) => text || '-',
    },
    {
      title: '成功/失败',
      key: 'stats',
      render: (_: any, record: any) => (
        <Space>
          <Tag color="success">{record.success_count}</Tag>
          <span>/</span>
          <Tag color="error">{record.fail_count}</Tag>
        </Space>
      ),
    },
    {
      title: '状态',
      dataIndex: 'is_active',
      key: 'is_active',
      render: (active: boolean) => (
        <Tag color={active ? 'success' : 'error'} icon={active ? <CheckCircleOutlined /> : <CloseCircleOutlined />}>
          {active ? '活跃' : '禁用'}
        </Tag>
      ),
    },
    {
      title: '操作',
      key: 'action',
      render: (_: any, record: any) => (
        <Space>
          <Button
            type="text"
            size="small"
            icon={record.is_active ? <SwapLeftOutlined /> : <SwapRightOutlined />}
            onClick={() => toggle(record.id)}
          />
          <Popconfirm title="确认删除？" onConfirm={() => del(record.id)}>
            <Button type="text" size="small" danger icon={<DeleteOutlined />} />
          </Popconfirm>
        </Space>
      ),
    },
  ]

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <div>
          <h1 style={{ fontSize: 24, fontWeight: 'bold', margin: 0 }}>代理管理</h1>
          <p style={{ color: '#7a8ba3', marginTop: 4 }}>共 {proxies.length} 个代理</p>
        </div>
        <Button icon={<ReloadOutlined spin={checking} />} onClick={check} loading={checking}>
          检测全部
        </Button>
      </div>

      <Card title="添加代理（每行一个）">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input.TextArea
            value={newProxy}
            onChange={(e) => setNewProxy(e.target.value)}
            placeholder="http://user:pass@host:port"
            rows={3}
            style={{ fontFamily: 'monospace' }}
          />
          <Space>
            <Input
              value={region}
              onChange={(e) => setRegion(e.target.value)}
              placeholder="地区标签 (如 US, SG)"
              style={{ width: 200 }}
            />
            <Button type="primary" icon={<PlusOutlined />} onClick={add}>
              添加
            </Button>
          </Space>
        </Space>
      </Card>

      <Card title="外部代理接口">
        <Space direction="vertical" style={{ width: '100%' }}>
          <Input
            value={proxyApiUrl}
            onChange={(e) => setProxyApiUrl(e.target.value)}
            placeholder="https://your-proxy-api.example.com/get?region={region}"
            prefix={<ApiOutlined />}
          />
          <Space>
            <Button icon={<SaveOutlined />} onClick={saveProxyApi} loading={savingApi}>
              保存接口
            </Button>
            <Button type="primary" onClick={fetchExternalProxy} loading={fetchingExternal}>
              测试获取
            </Button>
          </Space>
          <Typography.Text type="secondary">
            接口返回格式支持 `user:pass@host:port`，不会写入数据库。`get_next()` 会在本地代理池取不到时自动回退到这里。
          </Typography.Text>
          {externalProxy ? (
            <Typography.Text copyable style={{ fontFamily: 'monospace' }}>
              {externalProxy}
            </Typography.Text>
          ) : null}
        </Space>
      </Card>

      <Card>
        <Table
          rowKey="id"
          columns={columns}
          dataSource={proxies}
          loading={loading}
          pagination={false}
        />
      </Card>
    </div>
  )
}
