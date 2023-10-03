import { useState, useEffect } from 'react';
import { Box, Text, Truncate, LabelGroup, Label } from '@primer/react';
import { Table, DataTable, PageHeader } from '@primer/react/drafts';
import { requestAPI } from '../../jupyterlab/handler';

type OvhSSHKey = {
  id: number,
  name: string;
  publicKey: string;
  fingerPrint?: string;
  regions: string[];
}

const SSHKeysTab = () => {
  const [sshKeys, setSSHKeys] = useState(new Array<OvhSSHKey>());
  const [managedSSHKeys, setManagedSSHKeys] = useState(new Array<OvhSSHKey>());
  useEffect(() => {
    requestAPI<any>('ovh/keys')
    .then(data => {
      const sshKeys = (data.keys as [any]).map((key, id) => {
        return {
          id,
          name: key['name'],
          publicKey: key['publicKey'],
          regions: key['regions']
        } as OvhSSHKey
      });
      setSSHKeys(sshKeys);
      const items = data.managed_keys['items'];
      const managedSSHhKeys = (items as [any]).map((key, id) => {
        const status = key.status.create_sskey_fn;
        return {
          id,
          name: status['name'],
          publicKey: status['publicKey'],
          fingerPrint: status['fingerPrint'],
          regions: [], // key['regions']
        } as OvhSSHKey
      });
      console.log('---', managedSSHKeys)
      setManagedSSHKeys(managedSSHhKeys);
    })
    .catch(reason => {
      console.error(
        `Error while accessing the jupyter server clouder extension.\n${reason}`
      );
    });
  }, []);
  return (
    <>
      <PageHeader>
        <PageHeader.TitleArea>
          <PageHeader.Title>SSH Keys</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box mt={1}>
        <Table.Container>
          <Table.Title as="h2" id="ssh-managed-keys">
            Managed SSH Keys
          </Table.Title>
          <Table.Subtitle as="p" id="ssh-managed-keys-subtitle">
            List of managed SSH Keys.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="ssh-managed-keys"
            aria-describedby="ssh-managed-keys-subtitle" 
            data={managedSSHKeys}
            columns={[
              {
                header: 'Name',
                field: 'name',
                renderCell: row => <Text>{row.name}</Text>
              },
              {
                header: 'Public Key',
                field: 'publicKey',
                renderCell: row => (
                  <Truncate maxWidth={200} title={row.publicKey} expandable={true}>
                    {row.publicKey}
                  </Truncate>
                )
              },
              {
                header: 'Fingerprint',
                field: 'fingerPrint',
                renderCell: row => (
                  <Text>
                    {row.fingerPrint}
                  </Text>
                )
              },
              {
                header: 'Regions',
                field: 'regions',
                renderCell: row => {
                  return <LabelGroup>{row.regions.map(region => <Label variant="primary">{region}</Label>)}</LabelGroup>
                }
              },
            ]}
          />
        </Table.Container>
      </Box>
      <Box mt={1}>
        <Table.Container>
          <Table.Title as="h2" id="sshkeys">
            All SSH Keys
          </Table.Title>
          <Table.Subtitle as="p" id="sshkeys-subtitle">
            List of SSH Keys.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="sshkeys"
            aria-describedby="sshkeys-subtitle" 
            data={sshKeys}
            columns={[
              {
                header: 'Name',
                field: 'name',
                renderCell: row => <Text>{row.name}</Text>
              },
              {
                header: 'Public Key',
                field: 'publicKey',
                renderCell: row => (
                  <Truncate maxWidth={200} title={row.publicKey} expandable={true}>
                    {row.publicKey}
                  </Truncate>
                )
              },
              {
                header: 'Regions',
                field: 'regions',
                renderCell: row => {
                  return <LabelGroup>{row.regions.map(region => <Label variant="primary">{region}</Label>)}</LabelGroup>
                }
              },
            ]}
          />
        </Table.Container>
      </Box>
    </>
  )
}

export default SSHKeysTab;
