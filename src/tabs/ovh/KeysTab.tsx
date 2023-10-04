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

const KeysTab = () => {
  const [allSSHKeys, setAllSSHKeys] = useState(new Array<OvhSSHKey>());
  const [clouderSSHKeys, setClouderSSHKeys] = useState(new Array<OvhSSHKey>());
  useEffect(() => {
    requestAPI<any>('ovh/keys')
    .then(data => {
      const allSSHKeys = (data.ssh_keys as [any]).map((key, id) => {
        return {
          id,
          name: key['name'],
          publicKey: key['publicKey'],
          regions: key['regions']
        } as OvhSSHKey
      });
      setAllSSHKeys(allSSHKeys);
      const items = data.clouder_ssh_keys.items;
      const clouderSSHhKeys = (items as [any]).map((key, id) => {
        const status = key.status.create_sshkey_handler;
        return {
          id,
          name: status['name'],
          publicKey: status['publicKey'],
          fingerPrint: status['fingerPrint'],
          regions: (status['regions'] as string).split(","),
        } as OvhSSHKey
      });
      setClouderSSHKeys(clouderSSHhKeys);
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
          <PageHeader.Title>Keys</PageHeader.Title>
        </PageHeader.TitleArea>
      </PageHeader>
      <Box mt={1}>
        <Table.Container>
          <Table.Title as="h2" id="clouder-ssh-keys">
            Clouder SSH Keys
          </Table.Title>
          <Table.Subtitle as="p" id="clouder-ssh-keys-subtitle">
            List the Clouder SSH Keys.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="clouder-ssh-keys"
            aria-describedby="clouder-ssh-keys-subtitle" 
            data={clouderSSHKeys}
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
            All OVHcloud SSH Keys
          </Table.Title>
          <Table.Subtitle as="p" id="sshkeys-subtitle">
            List all the SSH Keys.
          </Table.Subtitle>
          <DataTable
            aria-labelledby="sshkeys"
            aria-describedby="sshkeys-subtitle" 
            data={allSSHKeys}
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

export default KeysTab;
